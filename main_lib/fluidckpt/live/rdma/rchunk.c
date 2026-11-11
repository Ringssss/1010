/* rchunk: the smallest RDMA data plane a chunk store needs.
 *
 * A source registers the host memory that holds its chunks and then does nothing: the destination
 * pulls byte ranges with RDMA READ, so no source CPU, no source GPU and no collective is involved.
 * That is what makes many sources -> one destination trivially parallel (one connection per source
 * and port, one thread per connection) and lets the puller switch source between two chunks.
 *
 * Connections use librdmacm over the IPoIB addresses (one subnet per HCA port), which is the one
 * address-resolution path verified on this fabric (rping). One protection domain per device; a
 * memory region must be registered on the device whose port a connection uses.
 *
 * Build: gcc -O2 -fPIC -shared -o librchunk.so rchunk.c -lrdmacm -libverbs -lpthread
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <pthread.h>
#include <rdma/rdma_cma.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAX_DEV 8
#define RD_ATOM 16              /* outstanding READs per connection (device max is 255) */

typedef struct {
    struct ibv_context *ctx;
    struct ibv_pd *pd;
    char name[64];
} rc_dev;

static rc_dev g_dev[MAX_DEV];
static int g_ndev = -1;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

typedef struct {
    struct rdma_event_channel *ec;
    struct rdma_cm_id *id;
    int dev;
} rc_conn;

int rc_init(void)
{
    pthread_mutex_lock(&g_lock);
    if (g_ndev < 0) {
        int n = 0;
        struct ibv_context **list = rdma_get_devices(&n);
        g_ndev = 0;
        for (int i = 0; list && i < n && g_ndev < MAX_DEV; i++) {
            struct ibv_pd *pd = ibv_alloc_pd(list[i]);
            if (!pd) continue;
            g_dev[g_ndev].ctx = list[i];
            g_dev[g_ndev].pd = pd;
            snprintf(g_dev[g_ndev].name, sizeof g_dev[g_ndev].name, "%s",
                     ibv_get_device_name(list[i]->device));
            g_ndev++;
        }
    }
    pthread_mutex_unlock(&g_lock);
    return g_ndev;
}

const char *rc_dev_name(int i) { return (i >= 0 && i < g_ndev) ? g_dev[i].name : ""; }

static int dev_of(struct ibv_context *ctx)
{
    for (int i = 0; i < g_ndev; i++)
        if (g_dev[i].ctx == ctx) return i;
    return -1;
}

/* Register [addr, addr+len) on device `dev`. remote=1 allows peers to READ it. */
void *rc_reg(int dev, void *addr, uint64_t len, int remote, uint32_t *lkey, uint32_t *rkey)
{
    if (dev < 0 || dev >= g_ndev) return NULL;
    int acc = IBV_ACCESS_LOCAL_WRITE | (remote ? IBV_ACCESS_REMOTE_READ : 0);
    struct ibv_mr *mr = ibv_reg_mr(g_dev[dev].pd, addr, len, acc);
    if (!mr) return NULL;
    *lkey = mr->lkey;
    *rkey = mr->rkey;
    return mr;
}

int rc_dereg(void *mr) { return ibv_dereg_mr((struct ibv_mr *)mr); }

static int make_qp(struct rdma_cm_id *id, int dev)
{
    struct ibv_qp_init_attr a;
    memset(&a, 0, sizeof a);
    a.qp_type = IBV_QPT_RC;
    a.cap.max_send_wr = 64;
    a.cap.max_recv_wr = 4;
    a.cap.max_send_sge = 1;
    a.cap.max_recv_sge = 1;
    a.sq_sig_all = 1;
    return rdma_create_qp(id, g_dev[dev].pd, &a);
}

/* ---- passive side: accept every connection and leave it alone ------------------------------ */
typedef struct {
    struct rdma_event_channel *ec;
    struct rdma_cm_id *listener;
} rc_server;

static void *serve_loop(void *arg)
{
    rc_server *s = arg;
    struct rdma_cm_event *ev;
    while (rdma_get_cm_event(s->ec, &ev) == 0) {
        struct rdma_cm_id *id = ev->id;
        int type = ev->event;
        if (type == RDMA_CM_EVENT_CONNECT_REQUEST) {
            int dev = dev_of(id->verbs);
            struct rdma_conn_param p;
            memset(&p, 0, sizeof p);
            p.responder_resources = RD_ATOM;
            p.initiator_depth = 0;
            p.rnr_retry_count = 7;
            if (dev < 0 || make_qp(id, dev) || rdma_accept(id, &p)) {
                fprintf(stderr, "rchunk: accept failed: %s\n", strerror(errno));
                rdma_reject(id, NULL, 0);
            }
            rdma_ack_cm_event(ev);
        } else if (type == RDMA_CM_EVENT_DISCONNECTED) {
            rdma_ack_cm_event(ev);
            if (id->qp) rdma_destroy_qp(id);
            rdma_destroy_id(id);
        } else {
            rdma_ack_cm_event(ev);
        }
    }
    return NULL;
}

/* Listen on ip:port (ip selects the HCA port). Returns 0 on success. */
int rc_serve(const char *ip, int port)
{
    rc_init();
    rc_server *s = calloc(1, sizeof *s);
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    if (inet_pton(AF_INET, ip, &a.sin_addr) != 1) return -1;
    s->ec = rdma_create_event_channel();
    if (!s->ec || rdma_create_id(s->ec, &s->listener, NULL, RDMA_PS_TCP)) return -2;
    if (rdma_bind_addr(s->listener, (struct sockaddr *)&a)) return -3;
    if (rdma_listen(s->listener, 64)) return -4;
    pthread_t t;
    if (pthread_create(&t, NULL, serve_loop, s)) return -5;
    pthread_detach(t);
    return 0;
}

/* ---- active side ---------------------------------------------------------------------------- */
static int wait_ev(struct rdma_event_channel *ec, int want)
{
    struct rdma_cm_event *ev;
    if (rdma_get_cm_event(ec, &ev)) return -1;
    int got = ev->event, st = ev->status;
    rdma_ack_cm_event(ev);
    if (got != want) {
        fprintf(stderr, "rchunk: cm event %s (status %d), wanted %s\n", rdma_event_str(got), st,
                rdma_event_str(want));
        return -1;
    }
    return 0;
}

/* Connect from local ip `src_ip` (selects the local HCA port) to ip:port. */
void *rc_connect(const char *src_ip, const char *ip, int port)
{
    rc_init();
    rc_conn *c = calloc(1, sizeof *c);
    struct sockaddr_in s, d;
    memset(&s, 0, sizeof s);
    memset(&d, 0, sizeof d);
    s.sin_family = d.sin_family = AF_INET;
    d.sin_port = htons(port);
    if (inet_pton(AF_INET, src_ip, &s.sin_addr) != 1 || inet_pton(AF_INET, ip, &d.sin_addr) != 1)
        goto fail;
    c->ec = rdma_create_event_channel();
    if (!c->ec || rdma_create_id(c->ec, &c->id, NULL, RDMA_PS_TCP)) goto fail;
    if (rdma_resolve_addr(c->id, (struct sockaddr *)&s, (struct sockaddr *)&d, 3000) ||
        wait_ev(c->ec, RDMA_CM_EVENT_ADDR_RESOLVED))
        goto fail;
    if (rdma_resolve_route(c->id, 3000) || wait_ev(c->ec, RDMA_CM_EVENT_ROUTE_RESOLVED)) goto fail;
    c->dev = dev_of(c->id->verbs);
    if (c->dev < 0 || make_qp(c->id, c->dev)) goto fail;
    struct rdma_conn_param p;
    memset(&p, 0, sizeof p);
    p.initiator_depth = RD_ATOM;
    p.responder_resources = 0;
    p.retry_count = 7;
    p.rnr_retry_count = 7;
    if (rdma_connect(c->id, &p) || wait_ev(c->ec, RDMA_CM_EVENT_ESTABLISHED)) goto fail;
    return c;
fail:
    fprintf(stderr, "rchunk: connect %s -> %s:%d failed: %s\n", src_ip, ip, port, strerror(errno));
    if (c->id) {
        if (c->id->qp) rdma_destroy_qp(c->id);
        rdma_destroy_id(c->id);
    }
    if (c->ec) rdma_destroy_event_channel(c->ec);
    free(c);
    return NULL;
}

int rc_conn_dev(void *conn) { return ((rc_conn *)conn)->dev; }

/* Pull len bytes from the peer's (raddr, rkey) into local (laddr, lkey). The range is cut into
 * `piece`-byte READs with at most `window` outstanding. Blocks until done; returns 0, or a
 * negative ibv_wc_status. Call from several threads on *different* connections for parallelism. */
int rc_read(void *conn, uint64_t raddr, uint32_t rkey, void *laddr, uint32_t lkey, uint64_t len,
            uint32_t piece, int window)
{
    rc_conn *c = conn;
    struct ibv_qp *qp = c->id->qp;
    struct ibv_cq *cq = qp->send_cq;
    if (window > RD_ATOM) window = RD_ATOM;
    if (window < 1) window = 1;
    uint64_t off = 0;
    int out = 0, idle = 0;
    while (off < len || out > 0) {
        while (off < len && out < window) {
            uint32_t m = (len - off > piece) ? piece : (uint32_t)(len - off);
            struct ibv_sge sge = {.addr = (uint64_t)laddr + off, .length = m, .lkey = lkey};
            struct ibv_send_wr wr, *bad;
            memset(&wr, 0, sizeof wr);
            wr.sg_list = &sge;
            wr.num_sge = 1;
            wr.opcode = IBV_WR_RDMA_READ;
            wr.send_flags = IBV_SEND_SIGNALED;
            wr.wr.rdma.remote_addr = raddr + off;
            wr.wr.rdma.rkey = rkey;
            if (ibv_post_send(qp, &wr, &bad)) return -1000 - errno;
            off += m;
            out++;
        }
        struct ibv_wc wc[RD_ATOM];
        int n = ibv_poll_cq(cq, RD_ATOM, wc);
        if (n < 0) return -2000;
        for (int i = 0; i < n; i++) {
            if (wc[i].status != IBV_WC_SUCCESS) return -(int)wc[i].status;
            out--;
        }
        if (n == 0) {
            if (++idle > 2000) {            /* stop burning the core once the wire is the wait */
                struct timespec ts = {0, 20000};
                nanosleep(&ts, NULL);
            }
        } else {
            idle = 0;
        }
    }
    return 0;
}

void rc_close(void *conn)
{
    rc_conn *c = conn;
    if (!c) return;
    rdma_disconnect(c->id);
    if (c->id->qp) rdma_destroy_qp(c->id);
    rdma_destroy_id(c->id);
    rdma_destroy_event_channel(c->ec);
    free(c);
}


#include <iostream>
#include <vector>
struct Request {
    std::vector<int> src, dst;
    int src_ranks(int rank) { return src.at(rank); }
    const std::vector<int>& dst_ranks() { return dst; }
};
int main() {
  using rank_t = int;
  for (int destinations : {1,2}) {
    Request storage{{0,1}, destinations == 1 ? std::vector<int>{2,3} : std::vector<int>{4,5,2,3}};
    Request* request = &storage;
    const int tp_size = 2;
    for (int tp_rank = 0; tp_rank < tp_size; ++tp_rank) {
      std::vector<rank_t> ranks_in_chain {request->src_ranks(tp_rank)};
        for (auto it = request->dst_ranks().begin() + tp_rank;
             it < request->dst_ranks().end();
             it += tp_size) {
            ranks_in_chain.push_back(*it);
        }

      std::cout << "tp" << destinations << ":" << tp_rank << "=";
      for (int rank : ranks_in_chain) std::cout << rank << ",";
      std::cout << "\n";
    }
  }
}

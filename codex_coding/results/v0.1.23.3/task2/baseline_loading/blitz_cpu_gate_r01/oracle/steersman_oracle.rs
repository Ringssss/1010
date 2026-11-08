use std::{
    collections::{HashMap, HashSet, VecDeque},
    sync::atomic::{AtomicUsize, Ordering},
    time::Instant,
};



/// TODO:@greenEggLy save real model path and send path to replica to load model from safetensor files
pub(crate) struct Steersman {
    /// machine_id :> [model_name, used_device, last_use_time]
    machine_cached_models: HashMap<usize, HashMap<String, (HashSet<usize>, Instant)>>,
    /// machine :> ranks, all local ranks holded by some machine
    machine_to_ranks: HashMap<usize, Vec<usize>>,
    /// rank :> machine, the machine hoding this rank
    rank_to_machine: HashMap<usize, usize>,
    /// rank :> model_name
    rank_to_model: HashMap<usize, String>,
    /// rank :> nvlink usage
    rank_nvlink_sem: HashMap<usize, AtomicUsize>, // rank -> whether rank's nvlink is available: true->available, false->unavailable
    model_path_map: HashMap<String, (String, f32)>, // model_name -> model_path,param_size_in_gb

    time_to_live_in_ms: u128,
}

#[derive(Debug)]
pub struct Model {
    pub model_name: String,
    pub model_path: String,
    pub parameter_size: f32,
}

impl Steersman {
    pub(crate) fn new(time_to_live_in_ms: u128) -> Self {
        Steersman {
            machine_cached_models: HashMap::new(),
            machine_to_ranks: HashMap::new(),
            rank_to_machine: HashMap::new(),
            rank_to_model: HashMap::new(),
            rank_nvlink_sem: HashMap::new(),
            model_path_map: HashMap::new(),
            time_to_live_in_ms,
        }
    }

    pub(crate) fn record_model_unloaded(&mut self, rank: usize) {
        let model_name = self.get_managed_model_name();
        
        let machine_id = self.rank_to_machine[&rank];
        if let Some(model_map) = self.machine_cached_models.get_mut(&machine_id) {
            if let Some(model_entry) = model_map.get_mut(&model_name) {
                model_entry.0.remove(&rank);
            }
        }
    }

    pub(crate) fn register_model(
        &mut self,
        model_name: String,
        model_path: String,
        param_size: f32,
    ) {
        
        self.model_path_map
            .insert(model_name, (model_path, param_size));
    }

    pub(crate) fn init_model_config(&mut self, model: Model) {
        let Model {
            model_name,
            model_path,
            parameter_size,
        } = model;
        // FIXME: hardcoded param_size
        self.register_model(model_name, model_path, parameter_size);
    }

    pub(crate) fn query_machine_id(&self, rank: usize) -> usize {
        self.rank_to_machine[&rank]
    }

    pub(crate) fn register_replica(&mut self, rank: usize, machine_id: usize) {
        self.rank_to_machine.insert(rank, machine_id);
        self.machine_to_ranks
            .entry(machine_id)
            .or_insert(Vec::new())
            .push(rank);
        self.rank_nvlink_sem.insert(rank, 0.into());
    }

    pub(crate) fn get_model_path(&self, model_name: &str) -> String {
        self.model_path_map
            .get(model_name)
            .expect(model_name)
            .0
            .clone()
    }

    pub(crate) fn get_model_param_size(&self, model_name: &str) -> f32 {
        self.model_path_map.get(model_name).expect(model_name).1
    }

    pub(crate) fn machine_has_cache(&self, rank: usize, model_name: &str) -> bool {
        let machine_id = self.rank_to_machine[&rank];
        if let Some(model_map) = self.machine_cached_models.get(&machine_id) {
            if let Some(model_entry) = model_map.get(model_name) {
                if model_entry.0.len() > 0
                    || model_entry.1.elapsed().as_millis() < self.time_to_live_in_ms as u128
                {
                    
                    return true;
                } else {
                    return false;
                }
            }
        }
        false
    }

    /// machine :> replica
    fn find_one_rank_within_cache(&self, machine_id: usize, model_name: &str) -> Option<usize> {
        if let Some(local_cached_models) = self.machine_cached_models.get(&machine_id) {
            if let Some((replica_indices, timestamp)) = local_cached_models.get(model_name) {
                if replica_indices.len() > 0
                    || timestamp.elapsed().as_millis() < self.time_to_live_in_ms as u128
                {
                    // first premise :: There is a rank w/ cache
                    // second premise :: LRU eviction triggered
                    // 
                    // let mut rng = rand::thread_rng();
                    for rank in replica_indices.iter() {
                        if self.rank_nvlink_sem[rank].load(Ordering::Acquire) == 0 {
                            return Some(*rank);
                        }
                    }
                    // \todo \post all ranks w/i Machine is Nvl casting...
                    return None;
                } else {
                    return None;
                }
            }
        }
        None
    }

    pub(crate) fn get_managed_model_name(&mut self) -> String {
        assert_eq!(self.model_path_map.len(), 1);
        let (model_name, _) = self.model_path_map.clone().into_iter().next().unwrap();
        model_name
    }

    /// notify steersman that model is loaded onto replica
    pub(crate) fn record_model_loaded(&mut self, rank: usize) {
        assert_eq!(self.model_path_map.len(), 1);
        let (model_name, _) = self.model_path_map.clone().into_iter().next().unwrap();
        
        assert!(self.rank_to_machine.contains_key(&rank));
        self.rank_to_model.insert(rank, model_name.clone());
        let machine_id = self.rank_to_machine[&rank];
        if let Some(local_cached_models) = self.machine_cached_models.get_mut(&machine_id) {
            // \post \exists one model cached on this machine
            if let Some((ranks, timestamp)) = local_cached_models.get_mut(&model_name) {
                // \post \exists rank' also caches this model on this machine
                ranks.insert(rank);
                *timestamp = Instant::now();
                
            } else {
                // \post rank is the first to cache this model
                let mut ranks = HashSet::new();
                ranks.insert(rank);
                local_cached_models.insert(model_name.to_string(), (ranks, Instant::now()));
                
            }
        } else {
            // \post \not \exists any model cached on this machine
            let mut local_cached_models = HashMap::new();
            let mut ranks = HashSet::new();
            ranks.insert(rank);
            local_cached_models.insert(model_name.to_string(), (ranks, Instant::now()));
            self.machine_cached_models
                .insert(machine_id, local_cached_models);
            
        }
    }

    /// notify steersman that model is loaded onto replica
    pub(crate) fn _record_model_loaded_v2(&mut self, replica_index: usize, model_name: &str) {
        
        assert!(self.rank_to_machine.contains_key(&replica_index));
        self.rank_to_model
            .insert(replica_index, String::from(model_name));
        let machine_id = self.rank_to_machine[&replica_index];
        if let Some(local_cached_models) = self.machine_cached_models.get_mut(&machine_id) {
            // \post \exists one model cached on this machine
            if let Some((ranks, timestamp)) = local_cached_models.get_mut(model_name) {
                // \post \exists rank' also caches this model on this machine
                ranks.insert(replica_index);
                *timestamp = Instant::now();
                
            } else {
                // \post rank is the first to cache this model
                let mut ranks = HashSet::new();
                ranks.insert(replica_index);
                local_cached_models.insert(model_name.to_string(), (ranks, Instant::now()));
                
            }
        } else {
            // \post \not \exists any model cached on this machine
            let mut local_cached_models = HashMap::new();
            let mut ranks = HashSet::new();
            ranks.insert(replica_index);
            local_cached_models.insert(model_name.to_string(), (ranks, Instant::now()));
            self.machine_cached_models
                .insert(machine_id, local_cached_models);
            
        }
    }

    /// return a vector<pair>, pair.0 is src_rank, pair.1 is dst_rank.
    /// if nvlink for dst_rank is unavailable, pair.0 is None
    pub(crate) fn assign_nvlink_chains(
        &mut self,
        model_name: &str,
        inactive_replica_indices: &Vec<usize>,
    ) -> Vec<(Option<usize>, usize)> {
        // machine_id :> rank
        let mut fst_rank_on_machines = HashMap::new();
        inactive_replica_indices
            .iter()
            .map(|&rank| {
                let machine_id = self.rank_to_machine[&rank];
                if fst_rank_on_machines.contains_key(&machine_id) {
                    (Some(*fst_rank_on_machines.get(&machine_id).unwrap()), rank)
                } else if let Some(src_rank) =
                    self.find_one_rank_within_cache(machine_id, model_name)
                {
                    fst_rank_on_machines.insert(machine_id, src_rank);
                    (Some(src_rank), rank)
                } else {
                    (None, rank)
                }
            })
            .collect()
    }

    // pub(crate) find_empty_slots_on_machine(machine_index: usize) -> Vec<usize> {

    // }

    /// currently extend NvlChain is not impl
    ///
    /// finally, this func provides cybernetician with a candidate set of choice
    ///     then, the cybernetcian choose an appropriate size
    /// Return Value: VecDeque<rank_number, [ranks]>
    pub(crate) fn assign_tanz_chains(
        &mut self,
        inactive_ranks: &Vec<usize>,
    ) -> VecDeque<(usize, Vec<usize>)> {
        let mut tanz_ranks_on_machines = VecDeque::new();
        for (machine_index, local_ranks) in self.machine_to_ranks.iter() {
            // FIXME: hardcoded machine
            let mut unloaded_ranks = local_ranks.clone();
            if let Some(local_cache) = self.machine_cached_models.get(machine_index) {
                for (_, (loaded_ranks, _)) in local_cache.iter() {
                    unloaded_ranks.retain(|x| !loaded_ranks.contains(x));
                    if unloaded_ranks.is_empty() {
                        break;
                    }
                }
            }
            if unloaded_ranks.len() > 0 {
                tanz_ranks_on_machines.push_back((unloaded_ranks.len(), unloaded_ranks));
            }
        }
        // NOTE: steersman has stale view, while planner has newer view
        // inactive rank must be unloaded ranks
        {
            let all_unloaded_ranks = tanz_ranks_on_machines
                .iter()
                .flat_map(|(_, ranks)| ranks.iter().cloned())
                .collect::<HashSet<usize>>();
            assert!(
                inactive_ranks
                    .iter()
                    .all(|x| all_unloaded_ranks.contains(x)),
                "Inconsistent view: inactive_ranks<{:?}> unloaded_ranks<{:?}>",
                inactive_ranks,
                all_unloaded_ranks
            );
        }
        tanz_ranks_on_machines.iter_mut().for_each(|(_, slots)| {
            slots.retain(|x| inactive_ranks.contains(x));
            slots.sort()
        });
        tanz_ranks_on_machines
    }

    pub(crate) fn wait_replica_nvlink(&mut self, rank: usize, cnt: usize) {
        assert!(
            self.rank_nvlink_sem.contains_key(&rank)
                && self.rank_nvlink_sem[&rank].load(Ordering::Acquire) == 0,
            "Rank<{}> nvlink semaphore = {}",
            rank,
            self.rank_nvlink_sem[&rank].load(Ordering::Relaxed)
        );
        self.rank_nvlink_sem[&rank].store(cnt, Ordering::Release);
    }

    pub(crate) fn post_replica_nvlink(&mut self, rank: usize) {
        assert!(
            self.rank_nvlink_sem.contains_key(&rank)
                && self.rank_nvlink_sem[&rank].load(Ordering::Acquire) > 0
        );
        self.rank_nvlink_sem[&rank].fetch_sub(1, Ordering::AcqRel);
    }
}

use std::iter::FromIterator;

#[derive(PartialEq)] enum ReplicaState { Inactive, RdmaSending }
fn main() {
    let mut s = Steersman::new(100000);
    s.register_model("qwen".into(), "processed".into(), 1.0);
    s.register_replica(0, 27); s.register_replica(2, 27); s.register_replica(4, 27);
    s.record_model_loaded(0);
    println!("local={:?}", s.assign_nvlink_chains("qwen", &vec![2,4]));
    s.wait_replica_nvlink(0, 1);
    println!("busy={:?}", s.assign_nvlink_chains("qwen", &vec![2]));
    s.post_replica_nvlink(0);
    s.record_model_unloaded(0);
    println!("ttl_empty={}", s.machine_has_cache(2, "qwen"));
    println!("ttl_no_sender={:?}", s.assign_nvlink_chains("qwen", &vec![2]));
    s.time_to_live_in_ms = 0;
    println!("expired={}", s.machine_has_cache(2, "qwen"));
    s.record_model_loaded(0);
    println!("loaded_beyond_ttl={}", s.machine_has_cache(2, "qwen"));
    let normal_prefill_replica_indices = VecDeque::from(vec![(1u32, 0usize)]);
    let normal_decode_replica_indices = VecDeque::from(vec![(2u32, 2usize), (3u32, 4usize)]);
    let shutting_decode_replica_indices = VecDeque::from(vec![(1u32, 6usize)]);
    let shutted_null_replica_indices = vec![8usize];
    let mut rdma_mrk_lck = (0..9).map(|_| ReplicaState::Inactive).collect::<Vec<_>>();
    rdma_mrk_lck[4] = ReplicaState::RdmaSending;
    let rdma_sending_replicas = if cfg!(feature = "unidirect") {
            Vec::from_iter(
                normal_prefill_replica_indices
                    .iter()
                    .map(|&(_, replica_index)| replica_index)
                    .chain(
                        normal_decode_replica_indices
                            .iter()
                            .map(|&(_, replica_index)| replica_index),
                    )
                    .chain(
                        shutting_decode_replica_indices
                            .into_iter()
                            .map(|(_, replica_index)| replica_index),
                    )
                    .chain(shutted_null_replica_indices.into_iter())
                    .filter(|replica_index| rdma_mrk_lck[*replica_index] == ReplicaState::Inactive),
            )
        } else {
            Vec::from_iter(
                normal_decode_replica_indices
                    .iter()
                    .map(|&(_, replica_index)| replica_index)
                    .chain(
                        shutting_decode_replica_indices
                            .into_iter()
                            .map(|(_, replica_index)| replica_index),
                    )
                    .chain(shutted_null_replica_indices.into_iter())
                    .filter(|replica_index| rdma_mrk_lck[*replica_index] == ReplicaState::Inactive),
            )
        };
    println!("sender_order={:?}", rdma_sending_replicas);
    let mut src_replica_indices = rdma_sending_replicas;
    let src_replica_index = src_replica_indices.pop().unwrap();
    println!("selected_source={}", src_replica_index);
}

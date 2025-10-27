# mlfq_sim.py
# Multi-Level Feedback Queue simulation (SimPy)
# Paste into a Jupyter cell and run. Requires simpy installed.

import simpy
import random
import statistics
import math
import numpy as np
from collections import deque, defaultdict, namedtuple

# Queue simulation mode
SINGLE_QUEUE_MODE = 0
NETWORK_QUEUE_MODE = 1
# Defaut simulations mode
QUEUE_SIMULATION_MODE = NETWORK_QUEUE_MODE

# ----------------------
# Helper: Confidence Interval (95%)
# ----------------------
def mean_ci_95(data):
    n = len(data)
    if n == 0:
        return (None, None, None)
    mean = statistics.mean(data)
    if n == 1:
        return (mean, mean, mean)
    stdev = statistics.stdev(data)
    # t critical for 95% ~ 1.96 approx for large n, use t_approx = 1.96
    z = 1.96
    se = stdev / math.sqrt(n)
    return (mean, mean - z*se, mean + z*se)

# ----------------------
# Analytical baseline: M/M/m formulas
# ----------------------
def MMm_metrics(arrival_rate, service_rate, m):
    """Compute analytical M/M/m performance metrics"""
    # Traffic intensity = Cpu utilization (rh0)
    rho = arrival_rate / (m * service_rate)
    if rho >= 1:
        return None  # unstable

    # Probability of zero job in the system (p0)
    sum_terms = sum(((m * rho)**k) / math.factorial(k) for k in range(m))
    p0 = 1.0 / (sum_terms + ((m * rho)**m) / (math.factorial(m) * (1 - rho)))

    # Probability that an arriving job must wait for service (Pw)
    pw = ((m * rho)**m / (math.factorial(m) * (1 - rho))) * p0

    # Throughput (λ_eff)
    lam_eff = arrival_rate

    # Average Service Time (1/μ)
    service_time = 1.0 / service_rate

    # Average Number of Jobs in Queue (Lq)
    Lq = (pw * rho) / (1 - rho)

    # Average Number of Jobs in System (L)
    L = Lq + (arrival_rate / service_rate)

    # Average Waiting Time in Queue (Wq)
    Wq = Lq / arrival_rate

    # Average Response Time = Average Turnaround Time (W)
    # because no I/O Blocked 
    W = Wq + 1.0 / service_rate

    return {
        'rho': rho,                     # Traffic intensity = Cpu utilization (rh0)
        'P0': p0,
        'Pw': pw,
        'Lq': Lq,
        'L': L,
        'Wq': Wq,                       # Average Waiting Time in Queue (Wq)
        'W': W,                         # Average Response Time = Average Turnaround Time (W)
        'lam_eff': lam_eff,             # Throughput (λ_eff)
        'service_time': service_time,   # Average service time (1/μ)
    }

# ----------------------
# Task object
# ----------------------
class Task:
    __slots__ = ('tid','arrival_time','remaining','total_service','level','last_enqueue_time','visits','cancelled')
    def __init__(self, tid, arrival_time, total_service):
        self.tid = tid
        self.arrival_time = arrival_time
        self.remaining = total_service
        self.total_service = total_service
        self.level = 0
        self.last_enqueue_time = arrival_time
        self.visits = 0
        self.cancelled = False  # for signal/cancel management

# ----------------------
# MLFQ System
# ----------------------
class MLFQSystem:
    def __init__(self,
                 name,                  # module name
                 env,                   # share env from netword
                 arrival_rate,          # lambda
                 service_rate,          # mu for CPU total work distribution
                 cpu_cores=1,           # number of CPU cores
                 num_levels=3,          # number of feedback levels
                 quantums=None,         # list of quantums per level (time units)
                 max_system_size=None,  # K total capacity (including in service + in queues + IO)
                 sim_time=10000,
                 seed=None):
        """
        name: module id
        env: shared simpy.Environment
        """
        self.name = name
        self.env = env
        self.arrival_rate = arrival_rate
        self.service_rate = service_rate
        self.cpu_cores = cpu_cores
        self.num_levels = num_levels
        if quantums is None:
            # default: level0 small quantum, then double
            self.quantums = [1.0 * (2**i) for i in range(num_levels)]
        else:
            self.quantums = quantums
        self.max_system_size = max_system_size
        self.sim_time = sim_time
        self.seed = seed

        # keep reference to network router will be set later
        # should be set by NetworkSimulator
        self.router = None

        # runtime vars
        self.cpu_store = None     # available core ids
        self.ready_queues = [ deque() for _ in range(self.num_levels) ]  # deque per level
        self.task_counter = 0

        # stats
        self.generated = 0
        self.served = 0
        self.dropped = 0
        self.completed_tasks = []
        self.wait_times_per_level = defaultdict(list)
        self.turnaround_times = []
        self.cpu_busy_time = 0.0
        self.cpu_service_times = []

        # trace/dup detection
        self.active_tasks = {}  # tid -> Task

    # ---------- utility ----------
    def current_total_in_system(self):
        """Count tasks in ready queues + cpu busy + io queue + io busy"""
        in_ready = sum(len(q) for q in self.ready_queues)
        cpu_busy = self.cpu_cores - len(self.cpu_store.items) if self.cpu_store is not None else 0
        return in_ready + cpu_busy

    # ---------- initialization ----------
    def init(self):
        # available CPU cores / IO servers
        self.cpu_store = simpy.Store(self.env, capacity=self.cpu_cores)
        for cid in range(self.cpu_cores):
            self.cpu_store.put(cid)
        # schedule processes
        self.env.process(self.arrival_generator())
        self.env.process(self.dispatcher())        # assign CPU cores to tasks
        # IO handler is invoked on demand (we spawn io_process for each IO-start)
        # optional background monitor for diagnostics could be here

    # ---------- arrival generator ----------
    def arrival_generator(self):
        while self.env.now < self.sim_time:
            inter = random.expovariate(self.arrival_rate)
            yield self.env.timeout(inter)
            self.handle_arrival()

    def handle_arrival(self):
        t = self.env.now
        self.task_counter += 1
        self.router.global_task_counter += 1
        tid = self.task_counter
        # sample total service requirement (Exp with mean 1/mu)
        total_service = random.expovariate(self.service_rate)
        task = Task(tid, t, total_service)
        # check max system size K
        if (self.max_system_size is not None) and (self.current_total_in_system() >= self.max_system_size):
            self.dropped += 1
            # we may log duplicate/drop signal; return
            return
        # accept task: push to level 0 queue
        task.last_enqueue_time = t
        task.level = 0
        self.ready_queues[0].append(task)
        self.generated += 1
        self.active_tasks[tid] = task

    # ---------- dispatcher ----------
    def dispatcher(self):
        """Continuously check for idle CPU cores and non-empty highest-level ready queue.
           When both available, start a cpu slice process for that task.
        """
        while self.env.now < self.sim_time:
            # if no tasks ready, wait a tiny bit
            if all(len(q)==0 for q in self.ready_queues):
                # no pending tasks; avoid busy-loop
                yield self.env.timeout(0.001)
                continue
            # if no core free, wait until one becomes free (or small timeout)
            if len(self.cpu_store.items) == 0:
                # wait small time to yield to cpu release events
                yield self.env.timeout(0.0005)
                continue
            # pick highest priority non-empty queue
            for lvl in range(self.num_levels):
                if len(self.ready_queues[lvl])>0:
                    task = self.ready_queues[lvl].popleft()
                    # record waiting time at this level
                    wait = self.env.now - task.last_enqueue_time
                    self.wait_times_per_level[lvl].append(wait)
                    # get a cpu core (non-blocking because we've checked cpu_store not empty)
                    core_id = yield self.cpu_store.get()
                    # spawn CPU slice process
                    self.env.process(self._cpu_slice(core_id, task, lvl))
                    break
            # small yield to allow other events
            yield self.env.timeout(0)

    # ---------- CPU slice ----------
    def _cpu_slice(self, core_id, task, lvl):
        """Process a task on core for a time slice = min(remaining, quantum[lvl])"""
        start = self.env.now
        quantum = self.quantums[lvl]
        slice_time = min(task.remaining, quantum)
        if slice_time < 0:
            slice_time = 0.0
        # "beginService" bookkeeping
        task.visits += 1
        # we count CPU busy time
        # we'll add to cpu_busy_time when service finishes (to support preemption / partial)
        # do the CPU slice
        if slice_time > 0:
            yield self.env.timeout(slice_time)
        elapsed = self.env.now - start
        task.remaining -= elapsed
        self.cpu_busy_time += elapsed
        self.cpu_service_times.append(elapsed)
        # endService: release core and perform routing
        # return core id
        yield self.cpu_store.put(core_id)
        # decide next: finished? go to IO? or demote and requeue
        if task.cancelled:
            # signal-based cancellation: remove from active tasks
            self.active_tasks.pop(task.tid, None)
            return
        if task.remaining <= 1e-12:
            # finished CPU work
            self.served += 1
            # task truly completed (no IO)
            turnaround = self.env.now - task.arrival_time
            self.turnaround_times.append(turnaround)
            self.completed_tasks.append(task)
            self.active_tasks.pop(task.tid, None)

            if self.router is not None:
                self.router.route_on_completion(task, from_module=self.name)
            return
        else:
            # not finished -> demote
            new_lvl = min(lvl+1, self.num_levels-1)
            task.level = new_lvl
            task.last_enqueue_time = self.env.now
            self.ready_queues[new_lvl].append(task)
            return

    # ---------- signal: cancel a task (intermediate canceling) ----------
    def cancel_task(self, tid):
        """Mark a task cancelled. When process reaches next checks it will remove it."""
        t = self.active_tasks.get(tid)
        if t:
            t.cancelled = True
            return True
        return False

    # ---------- run ----------
    def run(self):
        if self.env is not None:
            self.init()

    # ---------- results ----------
    def results(self):
        res = {}
        res['generated'] = self.generated   # total of task
        res['served_cpu'] = self.served     # total of task finished
        res['dropped'] = self.dropped       # total of task dropped
        res['avg_wait_per_job'] = (sum(sum(ws) for ws in self.wait_times_per_level.values()) / self.served) if self.served > 0 else 0.0
        res['avg_turnaround'] = statistics.mean(self.turnaround_times) if self.turnaround_times else 0.0 # Average turnaround time
        res['cpu_util'] = (self.cpu_busy_time / (self.cpu_cores * self.sim_time)) if self.sim_time>0 else 0.0
        res['cpu_service_mean'] = (sum(self.cpu_service_times) / self.served) if self.served > 0 else 0.0

        # --- Little/sample-based Lq estimator (Lq)---
        lam_hat = (self.served / self.sim_time) if self.sim_time > 0 else 0.0   # measured throughput
        Wq_hat = res['avg_wait_per_job']                                        # measured avg waiting time per job
        res['avg_number_in_queue'] = lam_hat * Wq_hat

        # --- System-level Little estimator (L) ---
        mu = (self.service_rate) if self.service_rate > 0 else 0.0
        res['avg_number_in_system'] = res['avg_number_in_queue'] + (lam_hat / mu if mu > 0 else 0.0)

        return res

# ----------------------
# Network Simulator
# ----------------------
class NetworkSimulator:
    def __init__(self, sim_time=10000, seed=None):
        self.env = simpy.Environment()
        self.modules = {}   # name -> ModuleMLFQ
        self.routing = {}   # name -> list of (next_name, prob) ; next_name None = exit
        self.sim_time = sim_time
        self.seed = seed
        self.global_task_counter = 0
        self.completed_tasks = []   # tasks that exit network
        self.external_generators = {}  # module_name -> arrival_rate for independent arrivals

    def add_module(self, name, arrival_rate, service_rate, cpu_cores, num_levels=3, quantums=None, max_size=None, sim_time = 10000, seed=None):
        mod = MLFQSystem(name, self.env, arrival_rate, service_rate, cpu_cores, num_levels, quantums, max_size, sim_time, seed)
        mod.router = self
        self.modules[name] = mod
        return mod

    def set_routing(self, from_name, routing_list):
        """
        routing_list: list of (to_name_or_None, prob)
        sum(prob) should be <= 1; remainder => exit (None)
        Example: [('Render',0.5), ('Sound',0.2)] means 50%->Render,20%->Sound, 30%->exit
        """
        self.routing[from_name] = routing_list

    def add_external_arrival(self, module_name, arrival_rate):
        """Add an independent Poisson external arrival stream to a module."""
        self.external_generators[module_name] = arrival_rate

    def route_on_completion(self, task, from_module):
        """Called by module when it finishes local service. Decide next hop."""
        rlist = self.routing.get(from_module, [])
        # compute cumulative distribution
        rnd = random.random()
        cum = 0.0
        for to_name, prob in rlist:
            cum += prob
            if rnd < cum:
                # route to to_name (if None means exit)
                if to_name is None:
                    # exit system
                    self.completed_tasks.append((task, self.env.now))
                else:
                    # send to module
                    self.modules[to_name].handle_arrival()
                return
        # if not returned, exit system
        self.completed_tasks.append((task, self.env.now))

    def run(self):
        if self.seed is not None:
            random.seed(self.seed)
        
        # initialize each module so they register their processes on the shared env
        for name, mod in self.modules.items():
            if hasattr(mod, 'run'):
                mod.run()

        self.env.run(until=self.sim_time)

    def gather_results(self):
        # per-module metrics
        mod_result = {}
        for name, m in self.modules.items():
            mod_result[name] = m.results()
        # end-to-end
        completed = len(self.completed_tasks)
        end_to_end_times = [(t.env_time - t.arrival_time) if False else (exit_time - task.arrival_time) for task, exit_time in self.completed_tasks]
        # above line uses exit_time - gen_time
        et_times = [exit_time - task.arrival_time for task, exit_time in self.completed_tasks]
        avg_number_in_system_net = sum(
            mod_result[name].get('avg_number_in_system', 0.0) for name in mod_result
        )
        overall = {
            'completed': completed,
            'avg_number_in_system_net': avg_number_in_system_net,
            'arrival_rate_net': completed / self.sim_time if self.sim_time > 0 else 0.0,
            'avg_end2end': sum(et_times) / completed if completed > 0 else 0.0
        }
        return {'modules': mod_result, 'overall': overall, 'completed_list': self.completed_tasks}

# ----------------------
# Runner to perform replications and comparisons
# ----------------------
def run_network_scenario(scenario, reps=10):
    """
    scenario is dict with:
      - sim_time
      - modules: dict name -> {cpu_cores, service_rate, num_levels, quantums, max_size, ext_arrival_rate}
      - routing: dict from_name -> [(to_name, prob), ...]
    """
    summaries = []
    for r in range(reps):
        print(f"--- Start replication: {r} ---")
        sim = NetworkSimulator(sim_time=scenario.get('sim_time',10000), seed=(scenario.get('seed',None) + r) if scenario.get('seed',None) is not None else None)
        # add modules
        for name, cfg in scenario['modules'].items():
            sim.add_module(name,
                           arrival_rate=cfg.get('ext_arrival_rate'),
                           service_rate=cfg.get('service_rate',1.0),
                           cpu_cores=cfg.get('cpu_cores',1),
                           num_levels=cfg.get('num_levels',3),
                           quantums=cfg.get('quantums', None),
                           max_size=cfg.get('max_size', None),
                           sim_time=scenario.get('sim_time',10000),
                           seed=cfg.get('seed',None))
            # external arrivals if present
            if cfg.get('ext_arrival_rate', None) is not None:
                sim.add_external_arrival(name, cfg['ext_arrival_rate'])
        # set routing
        for frm, rlist in scenario.get('routing', {}).items():
            sim.set_routing(frm, rlist)
        # run sim
        sim.run()
        res = sim.gather_results()
        summaries.append(res)
    print()
    # aggregate per-module numeric stats across reps
    agg = {}
    module_names = list(scenario['modules'].keys())
    for name in module_names:
        generated = []
        dropped = []
        throughput = []
        cpu_service_mean = []
        cpu_util = []
        avg_turnaround = []
        avg_wait_per_job = []
        avg_number_in_system = []
        avg_number_in_queue = []
        for s in summaries:
            mm = s['modules'][name]

            generated.append(mm['generated'])
            dropped.append(mm['dropped'])
            lam_eff = mm['served_cpu'] / scenario['sim_time']
            throughput.append(lam_eff)
            cpu_service_mean.append(mm['cpu_service_mean'])
            cpu_util.append(mm['cpu_util']),
            avg_turnaround.append(mm['avg_turnaround']),
            avg_wait_per_job.append(mm['avg_wait_per_job'])
            avg_number_in_queue.append(mm['avg_number_in_queue'])
            avg_number_in_system.append(mm['avg_number_in_system'])

        # helper to compute mean + 95% CI via mean_ci_95 and format missing data
        def statistics_95(samples):
            mean, lo, hi = mean_ci_95(samples)
            return {'mean': mean, '95ci': (lo, hi)}

        agg[name] = {
            'generated': statistics_95(generated),
            'dropped': statistics_95(dropped),
            'throughput': statistics_95(throughput),
            'cpu_service_mean': statistics_95(cpu_service_mean),
            'cpu_util': statistics_95(cpu_util),
            'avg_turnaround': statistics_95(avg_turnaround),
            'avg_wait_per_job': statistics_95(avg_wait_per_job),
            'avg_number_in_queue': statistics_95(avg_number_in_queue),
            'avg_number_in_system': statistics_95(avg_number_in_system)
        }
    # overall end-to-end aggregated
    completed = [s['overall']['completed'] for s in summaries]
    arrival_rate_net = [s['overall']['arrival_rate_net'] for s in summaries]
    avg_number_in_system_net = [s['overall']['avg_number_in_system_net'] for s in summaries]
    avg_e2e = [s['overall']['avg_end2end'] for s in summaries]
    overall = {'arrival_rate_net': statistics_95(arrival_rate_net), 'avg_number_in_system_net': statistics_95(avg_number_in_system_net), 'avg_e2e_mean': statistics_95(avg_e2e)}
    return {'module_agg': agg, 'summaries': summaries, 'overall': overall}

# ----------------------
# Extract scenarios
# ----------------------
def extract_all_modules(scenario):
    """
    Extract the entire module from the scenario.
    """
    modules = {}
    for name, cfg in scenario.get('modules', {}).items():
        workload = {
            'arrival_rate': cfg.get('ext_arrival_rate', 0.0),
            'service_rate': cfg['service_rate'],
            'cpu_cores': cfg.get('cpu_cores', 1),
            'num_levels': cfg.get('num_levels', 3),
            'quantums': cfg.get('quantums', None),
            'max_system_size': cfg.get('max_size', None),
            'sim_time': scenario.get('sim_time', cfg.get('sim_time', 10000)),
            'seed': scenario.get('seed', cfg.get('seed', None))
        }
        modules[name] = workload
    return modules

# ----------------------
# Helper build matrix P^T
# ----------------------
def _build_routing_matrix_T(scenario):
    """
    Build P^T matrix representation as dict-of-dicts where P_T[i][j] = prob from j -> i.
    Returns (names, P_col, gamma_list)
    """
    names = list(scenario['modules'].keys())
    n = len(names)
    name_to_idx = {name:i for i,name in enumerate(names)}
    # gamma (external arrivals)
    gamma = [scenario['modules'][name].get('ext_arrival_rate', 0.0) or 0.0 for name in names]
    # P^T columns: P_col[i] is dict mapping from-column j -> prob from j->i
    P_col = [defaultdict(float) for _ in range(n)]
    routing = scenario.get('routing', {})
    for j, from_name in enumerate(names):
        for to_name, prob in routing.get(from_name, []):
            if to_name is None:
                continue
            if to_name not in name_to_idx:
                # ignore unknown destination
                continue
            i = name_to_idx[to_name]
            P_col[i][j] += prob
    return names, P_col, gamma

# ----------------------
# Effective arrivals rate
# ----------------------
def compute_effective_arrivals(scenario, tol=1e-12, max_iter=10000):
    """
    Compute lambda_eff per module.
    Uses numpy solver if available, otherwise uses fixed-point iteration.
    Returns dict: {module_name: lambda_eff}
    """
    names, P_col, gamma = _build_routing_matrix_T(scenario)
    n = len(names)
    Gamma_total = sum(gamma)
    # Try numpy solve
    A = np.zeros((n,n), dtype=float)
    for i in range(n):
        for j in range(n):
            # P_col[i] gives probs from j->i
            A[i,j] = P_col[i].get(j, 0.0)
    # A currently is P^T; we need (I - P^T) * lam = gamma
    IminusP = np.eye(n) - A
    try:
        lam = np.linalg.solve(IminusP, np.array(gamma, dtype=float))
    except np.linalg.LinAlgError:
        raise RuntimeError("Analytical solver: (I - P^T) singular; routing may never exit.")
    return {names[i]: float(lam[i]) for i in range(n)}

# ----------------------
# Queue network analytical
# ----------------------
def network_analytical_summary(scenario):
    """
    Compute analytical (Jackson/M/M/c) metrics for each module and an overall end-to-end W_net.
    Prints nicely and returns a dict of results.
    """
    # extract gamma total to compute network-level W via Little
    names = list(scenario['modules'].keys())
    gamma = {name: scenario['modules'][name].get('ext_arrival_rate', 0.0) or 0.0 for name in names}
    Gamma_total = sum(gamma.values())
    if Gamma_total <= 0:
        print("Warning: total external arrival rate is zero. Nothing to analyze.")
        return {}

    # 1) compute effective lambdas
    lam_eff = compute_effective_arrivals(scenario)

    # 2) compute per-module M/M/c metrics using mmc_metrics
    per_module = {}
    L_total = 0.0
    for name in names:
        lam = lam_eff.get(name, 0.0)
        cfg = scenario['modules'][name]
        mu = cfg['service_rate']
        c = cfg['cpu_cores']
        metrics = MMm_metrics(lam, mu, c)
        if metrics is None:
            per_module[name] = {'lambda': lam, 'unstable': True}
            print(f"Module {name}: Analytical unstable (rho >= 1) with λ={lam:.6f}, μ={mu}, m={c}")
            continue
        per_module[name] = {'lambda': lam, **metrics}
        L_total += metrics['L']

    # 3) network end-to-end via Little: W_net = L_total / Gamma_total
    W_net = L_total / Gamma_total if Gamma_total>0 else None

    # 4) print nicely
    for name in names:
        info = per_module.get(name)
        if info is None:
            print(f"{name}: no data")
            continue
        if info.get('unstable', False):
            print(f"{name}: unstable analytical (rho >= 1). λ_eff={info['lambda']:.6f}")
            continue
        lam = info['lam_eff']
        mu = info['service_time'] and (1.0 / info['service_time']) or scenario['modules'][name]['service_rate']
        m = scenario['modules'][name]['cpu_cores']
        print(f"\n{name}:")
        print(f"λ={lam:.3f}, μ={mu:.3f}, m={m}")
        print(f"1. Throughput theoretical (λ_eff)          : {info['lam_eff']:.4f}")
        print(f"2. Average Service time (1/μ)              : {info['service_time']:.4f}")
        print(f"3. CPU Util theoretical (ρ)                : {info['rho']:.4f}")
        print(f"4. Average Turnaround time theoretical (W) : {info['W']:.4f}")
        print(f"5. Average waiting time theoretical (Wq)   : {info['Wq']:.4f}")
        print(f"6. Average number in queue (Lq)            : {info['Lq']:.4f}")
        print(f"7. Average number in system (L)            : {info['L']:.4f}")

    print("\nGame engine network:")
    print(f"1. Total external arrival rate (Γ)          : {Gamma_total:.6f}")
    print(f"2. Total expected jobs in network (L)       : {L_total:.6f}")
    if W_net is not None:
        print(f"3. Average end-to-end response time (W)     : {W_net:.6f}")
    else:
        print("Cannot compute W_net (Gamma_total=0).")

    return {'per_module': per_module, 'L_total': L_total, 'Gamma_total': Gamma_total, 'W_net': W_net}

# ----------------------
# Example scenarios
# ----------------------
if __name__ == "__main__":
    ######### Simulations #########
    print(f"Running network queue simulation mode........\n")
    # Network queue sub-module simulation
    # Define scenario with 3 modules: Render, AI, Sound
    scenario = {
        'sim_time': 2000,
        'seed': 259,
        'modules': {
            'Render': {'cpu_cores':2, 'service_rate':1.0, 'num_levels':3, 'quantums':[0.5,1.0,2.0], 'ext_arrival_rate':0.3},
            'AI':     {'cpu_cores':2, 'service_rate':0.8, 'num_levels':2, 'quantums':[0.5,1.0], 'ext_arrival_rate':0.1},
            'Sound':  {'cpu_cores':2, 'service_rate':0.9, 'num_levels':2, 'quantums':[0.5,1.0], 'ext_arrival_rate':0.4},
        },
        # Routing: after finishing in a module, it routes to the next with given prob
        # Format: from_module: [(to_module_or_None, prob), ...]
        # remainder probability means exit
        'routing': {
            'Render': [('AI', 0.1), ('Sound', 0.05)],  # 10% -> AI, 5% -> Sound, else exit
            'AI': [('Render', 0.2)],                   # 20% -> Render, else exit
            'Sound': []                                # no explicit routes => exit
        }
    }

    ######### Light workload #########
    print("Running 5 reps light workload...")
    out = run_network_scenario(scenario, reps=5)
    print("=== Light workload simulation measurement summary ===")
    for name, mm in out['module_agg'].items():
        def fmt(stat, fmt_mean="{:.2f}", fmt_ci="({:.2f}, {:.2f})"):
            if stat is None:
                return "N/A"
            mean = stat.get('mean', None)
            ci = stat.get('95ci', (None, None))
            lo, hi = ci if ci is not None else (None, None)
            if mean is None:
                return "N/A"
            try:
                mean_s = fmt_mean.format(mean)
            except Exception:
                mean_s = str(mean)
            
            if lo is None or hi is None:
                return f"{mean_s}"
            try:
                ci_s = fmt_ci.format(lo, hi)
            except Exception:
                ci_s = f"({lo}, {hi})"
            return f"{mean_s} ± {ci_s}"

        print(f"{name}:")
        print(f"Total task (mean ± 95%CI)                  : {fmt(mm.get('generated'), '{:.2f}', '({:.2f}, {:.2f})')}")
        print(f"Total task dropped (mean ± 95%CI)          : {fmt(mm.get('dropped'), '{:.2f}', '({:.2f}, {:.2f})')}")
        print(f"1. Throughput (mean ± 95%CI)               : {fmt(mm.get('throughput'), '{:.4f}', '({:.4f}, {:.4f})')}")
        print(f"2. Average Service time (mean ± 95%CI)     : {fmt(mm.get('cpu_service_mean'), '{:.3f}', '({:.3f}, {:.3f})')}")
        print(f"3. CPU Util (mean ± 95%CI)                 : {fmt(mm.get('cpu_util'), '{:.4f}', '({:.4f}, {:.4f})')}")
        print(f"4. Average Turnaround time (mean ± 95%CI)  : {fmt(mm.get('avg_turnaround'), '{:.4f}', '({:.4f}, {:.4f})')}")
        print(f"5. Average waiting time (mean ± 95%CI)     : {fmt(mm.get('avg_wait_per_job'), '{:.4f}', '({:.4f}, {:.4f})')}")
        print(f"6. Average number in queue (mean ± 95%CI)  : {fmt(mm.get('avg_number_in_queue'), '{:.4f}', '({:.4f}, {:.4f})')}")
        print(f"7. Average number in system (mean ± 95%CI) : {fmt(mm.get('avg_number_in_system'), '{:.4f}', '({:.4f}, {:.4f})')}")
        print()

    print("Game engine network:")
    print(f"1. Total external arrival rate (mean ± 95%CI)   : {fmt(out['overall']['arrival_rate_net'], '{:.4f}', '({:.4f}, {:.4f})')}")
    print(f"2. Total expected jobs in network (mean ± 95%CI)   : {fmt(out['overall']['avg_number_in_system_net'], '{:.4f}', '({:.4f}, {:.4f})')}")
    print(f"3. Average end-to-end response time (mean ± 95%CI)   : {fmt(out['overall']['avg_e2e_mean'], '{:.4f}', '({:.4f}, {:.4f})')}")
    print()

    ######### Analytical #########
    print("=== Mathematical formula analytical calculation summary ===")
    anal = network_analytical_summary(scenario)





# mlfq_sim.py
# Multi-Level Feedback Queue simulation (SimPy)
# Paste into a Jupyter cell and run. Requires simpy installed.

import simpy
import random
import statistics
import math
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
# Compare Simulation vs Analytical
# ----------------------
def math_formula_calculation(scenario):
    lam = scenario['arrival_rate']
    mu = scenario['service_rate']
    m = scenario['cpu_cores']
    analytical = MMm_metrics(lam, mu, m)
    if analytical is None:
        print("System unstable (rho >= 1), analytical formulas invalid.")
        return

    print(f"λ={lam:.3f}, μ={mu:.3f}, m={m}")
    print(f"1. Throughput theoretical (λ_eff)          : {analytical['lam_eff']:.4f}")
    print(f"2. Average Service time (1/μ)              : {analytical['service_time']:.4f}")
    print(f"3. CPU Util theoretical (ρ)                : {analytical['rho']:.4f}")
    print(f"4. Average Turnaround time theoretical (W) : {analytical['W']:.4f}")
    print(f"5. Average waiting time theoretical (Wq)   : {analytical['Wq']:.4f}")

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
        return res
    
# ----------------------
# Runner for replications & workloads
# ----------------------
def run_replications(scenario, reps=30):
    results = []
    for r in range(reps):
        print(f"--- Start replication: {r} ---")
        sys = MLFQSystem(
            arrival_rate=scenario['arrival_rate'],
            service_rate=scenario['service_rate'],
            cpu_cores=scenario.get('cpu_cores',1),
            num_levels=scenario.get('num_levels',3),
            quantums=scenario.get('quantums', None),
            max_system_size=scenario.get('max_system_size', None),
            sim_time=scenario.get('sim_time', 10000),
            seed=(scenario.get('seed',None) if scenario.get('seed',None) is None else scenario.get('seed')+r)
        )
        sys.run()
        res = sys.results()
        results.append(res)
        # print replications result at time
        # print_replication_result(r, res)
        # print(f"--- End replication: {r}---\n")

    print()

    # aggregate into dictionaries of lists for metrics
    agg = defaultdict(list)
    for r in results:
        agg['generated'].append(r['generated'])
        agg['dropped'].append(r['dropped'])

        lam_eff = r['served_cpu'] / scenario['sim_time']
        agg['throughput'].append(lam_eff)
        
        agg['cpu_service_mean'].append(r['cpu_service_mean'])
        agg['cpu_util'].append(r['cpu_util'])
        agg['avg_turnaround'].append(r['avg_turnaround'])
        agg['avg_wait_per_job'].append(r['avg_wait_per_job'])
        
    # compute mean & 95% CI
    summary = {}
    for k,v in agg.items():
        mean, lo, hi = mean_ci_95(v)
        summary[k] = {'mean':mean, '95ci':(lo,hi)}
    return summary

# ----------------------
# Print result replications
# ----------------------
def print_replication_result(i, res):
    print(f"--- Run {i} ---")
    print(f"Generated tasks      : {res['generated']}")
    print(f"Served (CPU done)    : {res['served_cpu']}")
    print(f"Dropped tasks        : {res['dropped']}")
    print(f"Avg turnaround time  : {res['avg_turnaround']:.4f}")
    print(f"CPU utilization      : {res['cpu_util']:.4f}")
    print(f"CPU mean service     : {res['cpu_service_mean']:.4f}")

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
        overall = {
            'completed': completed,
            'avg_end2end': statistics.mean(et_times) if et_times else 0.0
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
            'avg_wait_per_job': statistics_95(avg_wait_per_job)
        }
    # overall end-to-end aggregated
    completed = [s['overall']['completed'] for s in summaries]
    avg_e2e = [s['overall']['avg_end2end'] for s in summaries]
    overall = {'completed_mean': statistics.mean(completed), 'avg_e2e_mean': statistics.mean(avg_e2e)}
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
# Example scenarios
# ----------------------
if __name__ == "__main__":
    if QUEUE_SIMULATION_MODE == SINGLE_QUEUE_MODE:
        print(f"Running single queue simulation mode........\n")
        # Light workload
        light = {'arrival_rate':0.3, 'service_rate':1.0, 'cpu_cores':2,
                'num_levels':3, 'quantums':[0.5,1.0,2.0], 'max_system_size':None, 'sim_time':2000, 'seed':1}
        # Heavy workload (near overload)
        heavy = {'arrival_rate':1.1, 'service_rate':1.0, 'cpu_cores':2,
                'num_levels':3, 'quantums':[0.5,1.0,2.0], 'max_system_size':200, 'sim_time':2000, 'seed':10}

        ######### Light workload #########
        print("Running 10 reps light workload...")
        out_light = run_replications(light, reps=10)
        print("=== Light workload simulation measurement summary ===")
        for i, (metric, data) in enumerate(out_light.items(), start=-1):
            if metric in ["generated", "dropped"]:
                continue
            mean = data['mean']
            ci_lo, ci_hi = data['95ci']
            print(f"{i}. {metric:30s}: mean={mean:.4f}, 95% CI=({ci_lo:.4f}, {ci_hi:.4f})")

        print("=== Mathematical formula calculation summary ===")
        math_formula_calculation(light)
        print("\n")

        ######### Heavy workload #########
        # print("Running 10 reps heavy workload...")
        # out_heavy = run_replications(heavy, reps=10)
        # print("=== Heavy workload simulation measurement summary ===")
        # for metric, data in out_heavy.items():
        #     mean = data['mean']
        #     ci_lo, ci_hi = data['95ci']
        #     print(f"{metric:25s}        : mean={mean:.4f},95% CI=({ci_lo:.4f}, {ci_hi:.4f})")

        # print("=== Mathematical formula calculation summary ===")
        # math_formula_calculation(heavy)
        # print("\n")

        # # Single full run for inspection and detailed results
        # sys = MLFQSystem(arrival_rate=0.6, service_rate=1.0, cpu_cores=2, io_servers=2,
        #                  io_rate=1.0, num_levels=3, quantums=[0.5,1.0,2.0], p_io=0.25,
        #                  max_system_size=300, sim_time=5000, seed=42)
        # sys.run()
        # print("Single run metrics:", sys.results())
    elif QUEUE_SIMULATION_MODE == NETWORK_QUEUE_MODE:
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
        # Thay thế block in hiện tại bằng đoạn này
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
            print()

        print("--> Overall completed tasks mean:", out['overall']['completed_mean'], "avg end-to-end mean:", out['overall']['avg_e2e_mean'])
        print()

        print("=== Mathematical formula calculation summary ===")
        modules = extract_all_modules(scenario)

        for name, mod in modules.items():
            print(f"{name}:")
            math_formula_calculation(mod)
            print()
    else:
        print(f"Error: Invalid QUEUE_MODE '{QUEUE_SIMULATION_MODE}'.")

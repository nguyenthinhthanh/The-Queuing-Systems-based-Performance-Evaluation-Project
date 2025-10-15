# mlfq_sim.py
# Multi-Level Feedback Queue simulation (SimPy)
# Paste into a Jupyter cell and run. Requires simpy installed.

import simpy
import random
import statistics
import math
from collections import deque, defaultdict, namedtuple

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
                 arrival_rate,          # lambda
                 service_rate,          # mu for CPU total work distribution
                 cpu_cores=1,           # number of CPU cores
                 io_servers=1,          # number of IO servers
                 io_rate=1.0,           # mu_io
                 num_levels=3,          # number of feedback levels
                 quantums=None,         # list of quantums per level (time units)
                 p_io=0.2,              # prob go to IO after finishing CPU burst
                 max_system_size=None,  # K total capacity (including in service + in queues + IO)
                 sim_time=10000,
                 seed=None):
        self.arrival_rate = arrival_rate
        self.service_rate = service_rate
        self.cpu_cores = cpu_cores
        self.io_servers = io_servers
        self.io_rate = io_rate
        self.num_levels = num_levels
        if quantums is None:
            # default: level0 small quantum, then double
            self.quantums = [1.0 * (2**i) for i in range(num_levels)]
        else:
            self.quantums = quantums
        self.p_io = p_io
        self.max_system_size = max_system_size
        self.sim_time = sim_time
        self.seed = seed

        # runtime vars
        self.env = None
        self.cpu_store = None     # available core ids
        self.io_store = None      # available io server ids
        self.ready_queues = [ deque() for _ in range(self.num_levels) ]  # deque per level
        self.io_queue = deque()
        self.task_counter = 0

        # stats
        self.generated = 0
        self.served = 0
        self.dropped = 0
        self.completed_tasks = []
        self.wait_times_per_level = defaultdict(list)
        self.turnaround_times = []
        self.cpu_busy_time = 0.0
        self.io_busy_time = 0.0
        self.cpu_service_times = []
        self.io_service_times = []

        # trace/dup detection
        self.active_tasks = {}  # tid -> Task

    # ---------- utility ----------
    def current_total_in_system(self):
        """Count tasks in ready queues + cpu busy + io queue + io busy"""
        in_ready = sum(len(q) for q in self.ready_queues)
        cpu_busy = self.cpu_cores - len(self.cpu_store.items) if self.cpu_store is not None else 0
        in_ioq = len(self.io_queue)
        io_busy = self.io_servers - len(self.io_store.items) if self.io_store is not None else 0
        return in_ready + cpu_busy + in_ioq + io_busy

    # ---------- initialization ----------
    def init(self):
        if self.seed is not None:
            random.seed(self.seed)
        self.env = simpy.Environment()
        # available CPU cores / IO servers
        self.cpu_store = simpy.Store(self.env, capacity=self.cpu_cores)
        for cid in range(self.cpu_cores):
            self.cpu_store.put(cid)
        self.io_store = simpy.Store(self.env, capacity=self.io_servers)
        for iid in range(self.io_servers):
            self.io_store.put(iid)
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
            self._handle_arrival()

    def _handle_arrival(self):
        t = self.env.now
        self.task_counter += 1
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
        # "beginService" bookkeeping
        task.visits += 1
        # we count CPU busy time
        # we'll add to cpu_busy_time when service finishes (to support preemption / partial)
        # do the CPU slice
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
            # maybe go to IO (simulate CPU <-> IO bursts before task terminates). using p_io
            if random.random() < self.p_io:
                # send to IO
                task.last_enqueue_time = self.env.now
                self.io_queue.append(task)
                # spawn io_process if io server available (we spawn always, handler will get server)
                self.env.process(self._io_process_if_idle())
            else:
                # task truly completed (no IO)
                turnaround = self.env.now - task.arrival_time
                self.turnaround_times.append(turnaround)
                self.completed_tasks.append(task)
                self.active_tasks.pop(task.tid, None)
            return
        else:
            # not finished -> demote
            new_lvl = min(lvl+1, self.num_levels-1)
            task.level = new_lvl
            task.last_enqueue_time = self.env.now
            self.ready_queues[new_lvl].append(task)
            return

    # ---------- IO handler ----------
    def _io_process_if_idle(self):
        """If there are tasks in io_queue and io servers free, start a service."""
        while len(self.io_queue) > 0 and len(self.io_store.items) > 0:
            task = self.io_queue.popleft()
            iid = yield self.io_store.get()
            # spawn actual io process
            self.env.process(self._io_service(iid, task))
        # done

    def _io_service(self, iid, task):
        start = self.env.now
        # sample io service time
        io_t = random.expovariate(self.io_rate)
        yield self.env.timeout(io_t)
        elapsed = self.env.now - start
        self.io_busy_time += elapsed
        self.io_service_times.append(elapsed)
        # release io server
        yield self.io_store.put(iid)
        # after IO, route back to ready queue (we choose to return to level 0)
        if task.cancelled:
            self.active_tasks.pop(task.tid, None)
            return
        task.level = 0
        task.last_enqueue_time = self.env.now
        self.ready_queues[0].append(task)

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
        if self.env is None:
            self.init()
        self.env.run(until=self.sim_time)

    # ---------- results ----------
    def results(self):
        res = {}
        res['generated'] = self.generated
        res['served_cpu'] = self.served
        res['dropped'] = self.dropped
        res['avg_wait_per_level'] = {lvl: (statistics.mean(ws) if ws else 0.0) for lvl,ws in self.wait_times_per_level.items()}
        res['avg_turnaround'] = statistics.mean(self.turnaround_times) if self.turnaround_times else 0.0
        res['cpu_util'] = (self.cpu_busy_time / (self.cpu_cores * self.sim_time)) if self.sim_time>0 else 0.0
        res['io_util'] = (self.io_busy_time / (self.io_servers * self.sim_time)) if self.sim_time>0 else 0.0
        res['cpu_slices_mean'] = statistics.mean(self.cpu_service_times) if self.cpu_service_times else 0.0
        res['io_slices_mean'] = statistics.mean(self.io_service_times) if self.io_service_times else 0.0
        return res

# ----------------------
# Runner for replications & workloads
# ----------------------
def run_replications(scenario, reps=30):
    results = []
    for r in range(reps):
        sys = MLFQSystem(
            arrival_rate=scenario['arrival_rate'],
            service_rate=scenario['service_rate'],
            cpu_cores=scenario.get('cpu_cores',1),
            io_servers=scenario.get('io_servers',1),
            io_rate=scenario.get('io_rate',1.0),
            num_levels=scenario.get('num_levels',3),
            quantums=scenario.get('quantums', None),
            p_io=scenario.get('p_io', 0.2),
            max_system_size=scenario.get('max_system_size', None),
            sim_time=scenario.get('sim_time', 10000),
            seed=(scenario.get('seed',None) if scenario.get('seed',None) is None else scenario.get('seed')+r)
        )
        sys.run()
        res = sys.results()
        results.append(res)
    # aggregate into dictionaries of lists for metrics
    agg = defaultdict(list)
    for r in results:
        agg['generated'].append(r['generated'])
        agg['served_cpu'].append(r['served_cpu'])
        agg['dropped'].append(r['dropped'])
        agg['avg_turnaround'].append(r['avg_turnaround'])
        agg['cpu_util'].append(r['cpu_util'])
    # compute mean & 95% CI
    summary = {}
    for k,v in agg.items():
        mean, lo, hi = mean_ci_95(v)
        summary[k] = {'mean':mean, '95ci':(lo,hi), 'samples':v}
    return summary

# ----------------------
# Example scenarios
# ----------------------
if __name__ == "__main__":
    # Light workload
    light = {'arrival_rate':0.3, 'service_rate':1.0, 'cpu_cores':2, 'io_servers':1, 'io_rate':0.8,
             'num_levels':3, 'quantums':[0.5,1.0,2.0], 'p_io':0.3, 'max_system_size':None, 'sim_time':2000, 'seed':1}
    # Heavy workload (near overload)
    heavy = {'arrival_rate':1.1, 'service_rate':1.0, 'cpu_cores':2, 'io_servers':1, 'io_rate':0.8,
             'num_levels':3, 'quantums':[0.5,1.0,2.0], 'p_io':0.3, 'max_system_size':200, 'sim_time':2000, 'seed':10}

    print("Running 10 reps light (demo)...")
    out_light = run_replications(light, reps=10)
    print(out_light['avg_turnaround'])

    print("Running 10 reps heavy (demo)...")
    out_heavy = run_replications(heavy, reps=10)
    print(out_heavy['avg_turnaround'])

    # Single full run for inspection and detailed results
    sys = MLFQSystem(arrival_rate=0.6, service_rate=1.0, cpu_cores=2, io_servers=2,
                     io_rate=1.0, num_levels=3, quantums=[0.5,1.0,2.0], p_io=0.25,
                     max_system_size=300, sim_time=5000, seed=42)
    sys.run()
    print("Single run metrics:", sys.results())

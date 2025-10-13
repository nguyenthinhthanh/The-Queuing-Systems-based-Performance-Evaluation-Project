# prototypical_mmck_sim.py
import simpy
import random
import statistics

# Event list giữ các sự kiện sắp xảy ra theo thứ tự thời gian (PriorityQueue theo thời gian).
# schedule() = đưa event mới vào event list; một mô phỏng event-driven truyền thống sẽ lấy event sớm nhất ra xử lý.
# validate(serverid) dùng để kiểm tra capacity (ví dụ: nếu maxQueueSize đã đầy thì validate trả NOT_OK → event bị drop).
# Nếu không dùng K (K = None), hệ thống không giới hạn queue → luôn chấp nhận event.
# beginService() mẫu thời gian phục vụ rồi schedule(endService) → đây là pattern “lên lịch end service” thay vì chờ trực tiếp.
# Prototype là mô tả event-driven (có event list). SimPy là discrete-event framework — nó cung cấp event scheduling & timing, nên ta không cần tự quản lý PriorityQueue thủ công (SimPy thay bạn xử lý event list), nhưng ta có thể mô phỏng hành vi prototype (schedule/validate/begin/end) bằng SimPy rất sát.

class PrototypeQueueSystem:
    def __init__(self, arrival_rate, service_rate, num_servers=1, max_system_size=None, sim_time=1000, seed=None):
        """
        arrival_rate: lambda (arrivals per time unit)
        service_rate: mu (service completions per time unit)
        num_servers: number of servers (m)
        max_system_size: K (total capacity including servers). None means infinite.
        sim_time: simulation run time
        """
        self.arrival_rate = arrival_rate
        self.service_rate = service_rate
        self.num_servers = num_servers
        self.max_system_size = max_system_size
        self.sim_time = sim_time
        self.seed = seed

        # runtime structures (initialized in init)
        self.env = None
        self.server_store = None  # store of server ids for getServer/putServer
        self.resource = None      # resource used for implicit queue counting
        self.customer_counter = 0

        # stats
        self.wait_times = []
        self.served = 0
        self.dropped = 0
        self.start_times = {}

    def init(self):
        """Set up environment, servers, and schedule the arrival generator.
           This corresponds to the 'system declaration' step where data generation is created."""
        if self.seed is not None:
            random.seed(self.seed)

        self.env = simpy.Environment()
        # Resource here is only used to maintain queue ordering and counts;
        # Server allocation (serverid) managed by server_store.
        self.resource = simpy.Resource(self.env, capacity=self.num_servers)
        # Create a store with server ids [0..num_servers-1]
        self.server_store = simpy.Store(self.env, capacity=self.num_servers)
        for sid in range(self.num_servers):
            # initially all servers available
            self.server_store.put(sid)

        # schedule the arrival generator (this is data generation step)
        self.env.process(self._arrival_generator())  # equivalent to schedule(self.arrival,...)

    # ---------- scheduling helper ----------
    def schedule(self, event_callable, delay=0, *args, **kwargs):
        """Schedule an event after 'delay' time units. This maps to schedule() in the prototype."""
        # We'll create a small process that waits delay then calls event_callable
        def _delayed():
            if delay > 0:
                yield self.env.timeout(delay)
            # call the event (could be a generator function)
            res = event_callable(*args, **kwargs)
            # if the event callable returns a generator (SimPy process), yield it
            if hasattr(res, '__iter__'):
                yield from res
            return
        # register as simpy process
        self.env.process(_delayed())

    # ---------- arrival ----------
    def _arrival_generator(self):
        """Arrival event generator — schedules arrivals until sim_time.
           This corresponds to init() scheduling arrival events in the prototype."""
        while self.env.now < self.sim_time:
            inter = random.expovariate(self.arrival_rate)
            yield self.env.timeout(inter)
            # schedule arrival (no delay)
            self.schedule(self.arrival)

    def arrival(self):
        """Handle raw arrival: increment counter, validate capacity, then either queue or drop.
           If a server is available, schedule beginService immediately."""
        self.customer_counter += 1
        cid = self.customer_counter
        # check total in node: users + queue
        total_in_node = len(self.resource.users) + len(self.resource.queue)
        if (self.max_system_size is not None) and (total_in_node >= self.max_system_size):
            # drop
            self.dropped += 1
            return
        # not dropped: create a customer process that requests resource (this models FIFO waiting)
        # We schedule the process so that it joins the resource queue
        self.env.process(self._customer_process(cid))

    def _customer_process(self, cid):
        arrival_time = self.env.now
        # request resource -> join queue if busy
        with self.resource.request() as req:
            yield req  # wait until granted (this implements queue ordering)
            wait = self.env.now - arrival_time
            self.wait_times.append(wait)
            # if there is a free server id, begin service now by scheduling beginService
            # getServer() done by trying to get from server_store (non-blocking if available)
            # But server_store.get() is a yield; however if capacity must be honored with resource,
            # we know we hold a resource slot, so server_store.get() must succeed immediately.
            serverid = yield self.server_store.get()
            # schedule endService with sampled service time (delay = service_time)
            service_time = random.expovariate(self.service_rate)
            # beginService bookkeeping
            self._beginService(serverid, cid)
            # schedule endService after service_time (we simply yield timeout here)
            yield self.env.timeout(service_time)
            # end service actions
            self._endService(serverid, cid, service_time)

    def _beginService(self, serverid, cid):
        # decrease available servers (implicitly by removing an id from server_store)
        # any other bookkeeping can be done here
        pass  # we already removed serverid via get()

    def _endService(self, serverid, cid, service_time):
        # return serverid to the store (server becomes available)
        self.server_store.put(serverid)
        self.served += 1
        # optional: record service_time etc.

    # ---------- run ----------
    def run(self):
        if self.env is None:
            self.init()
        self.env.run(until=self.sim_time)

    def results(self):
        return {
            'generated': self.customer_counter,
            'served': self.served,
            'dropped': self.dropped,
            'avg_wait': statistics.mean(self.wait_times) if self.wait_times else 0.0,
            'max_wait': max(self.wait_times) if self.wait_times else 0.0
        }

# ---------------- example usage ----------------
if __name__ == "__main__":
    random.seed(123)
    system = PrototypeQueueSystem(arrival_rate=0.8, service_rate=1.2,
                                 num_servers=2, max_system_size=5, sim_time=1000, seed=1)
    system.init()   # optional, run() will call init if not called
    system.run()
    print(system.results())

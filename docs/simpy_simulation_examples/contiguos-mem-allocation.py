# Install necessary libraries for Colab/Jupyter environment
# The ! prefix tells the environment to execute the command in the shell
import simpy
import random
import numpy as np

# --- 1. CONFIGURATION PARAMETERS ---
TOTAL_MEMORY_SIZE = 2048  # M: Total Memory Size (KB)
BLOCK_SIZES = [256, 512, 768, 1024]  # B1, B2, B3, B4 (KB)
MAX_PROCESSES = 25  # Limit the total number of processes to observe fragmentation

# Exponential Distribution Parameters (Lambda is the rate, used as 1/mean)
AVG_ARRIVAL_TIME = 5.0  # Mean inter-arrival time (time units)
AVG_HOLD_TIME = 10.0    # Mean time process holds memory (time units)

# --- 2. CUSTOM MEMORY MANAGER ---

class MemoryManager:
    """
    Manages contiguous memory segments using the First Fit allocation strategy.
    Memory is tracked as a list of (start, end, status, pid) tuples.
    """
    def __init__(self, size):
        self.size = size
        # Initial state: one large free block
        self.segments = [(0, size, 'FREE', None)]
        self.allocated_blocks = {} # {pid: (start, end)}
        self.next_pid = 1
        self.failed_allocations = 0 # NEW: Counter for initial allocation failures

    def allocate(self, requested_size):
        """Attempts to allocate a contiguous block using First Fit."""
        
        # Iterate through segments using First Fit
        for i, (start, end, status, pid) in enumerate(self.segments):
            if status == 'FREE':
                free_size = end - start
                
                if free_size >= requested_size:
                    # Found a fit!
                    
                    new_pid = self.next_pid
                    self.next_pid += 1

                    # 1. Define the allocated segment
                    allocated_end = start + requested_size
                    new_allocated_segment = (start, allocated_end, 'USED', new_pid)
                    
                    # 2. Handle the remaining free segment (if any)
                    if allocated_end < end:
                        # Split the free block: insert allocated block and keep remaining free block
                        remaining_free_segment = (allocated_end, end, 'FREE', None)
                        self.segments[i] = new_allocated_segment
                        self.segments.insert(i + 1, remaining_free_segment)
                    else:
                        # Exact fit: just replace the free block with the allocated block
                        self.segments[i] = new_allocated_segment
                    
                    # Store allocation record for release tracking
                    self.allocated_blocks[new_pid] = (start, allocated_end)
                    return True, new_pid

        return False, None # Allocation failed (fragmentation or full)

    def release(self, release_pid):
        """Frees the segment associated with the PID and merges adjacent free blocks."""
        
        released = False
        # 1. Find the segment to free by PID and mark it FREE
        for i, (start, end, status, pid) in enumerate(self.segments):
            if status == 'USED' and pid == release_pid:
                self.segments[i] = (start, end, 'FREE', None)
                released = True
                break
        
        if not released:
             return False

        if release_pid in self.allocated_blocks:
            del self.allocated_blocks[release_pid]
            
        # 2. Clean up (merge adjacent free blocks)
        self._merge_free_blocks()
        return True

    def _merge_free_blocks(self):
        """Helper to merge adjacent FREE segments to combat fragmentation."""
        i = 0
        while i < len(self.segments) - 1:
            curr_status = self.segments[i][2]
            next_status = self.segments[i+1][2]
            
            if curr_status == 'FREE' and next_status == 'FREE':
                curr_start = self.segments[i][0]
                next_end = self.segments[i+1][1]
                
                # Replace current segment with merged segment
                self.segments[i] = (curr_start, next_end, 'FREE', None)
                del self.segments[i+1] # Remove the next block
            else:
                i += 1
    
    def get_stats(self):
        """Calculates current memory usage and fragmentation stats."""
        total_used = sum(end - start for start, end, status, _ in self.segments if status == 'USED')
        total_free = self.size - total_used
        
        # External Fragmentation: Largest contiguous free block
        largest_free_block = 0
        num_free_blocks = 0
        for start, end, status, _ in self.segments:
            if status == 'FREE':
                size = end - start
                largest_free_block = max(largest_free_block, size)
                num_free_blocks += 1

        return {
            'used': total_used,
            'free': total_free,
            'largest_free': largest_free_block,
            'num_free_blocks': num_free_blocks,
            'failed_allocations': self.failed_allocations # NEW: Pass the counter
        }

# --- 3. SIMPY PROCESSES ---

def process_runner(env, mem_manager):
    """The process that requests, holds, and releases memory."""
    
    requested_size = random.choice(BLOCK_SIZES)
    
    # We use a unique ID before allocation for initial logging
    temp_id = mem_manager.next_pid 
    print(f"[{env.now:5.1f}] P-ID {temp_id} arrives, requesting {requested_size}KB.")
    
    # SimPy loop to attempt allocation (blocks until successful)
    initial_attempt = True # NEW: Flag to only count the first failure per process
    while True:
        success, pid = mem_manager.allocate(requested_size)
        
        if success:
            break
        else:
            # Allocation failed (fragmentation or full).
            if initial_attempt:
                mem_manager.failed_allocations += 1 # Increment failure count (only once per process)
                initial_attempt = False
                
                # NEW: Log memory fragmented when request fails
                stats = mem_manager.get_stats()
                print(f"[{env.now:5.1f}] P-ID {temp_id}: Allocation FAILED for {requested_size}KB.")
                print(f"                                (Memory fragmented: Total Free {stats['free']}KB, Largest Block {stats['largest_free']}KB). Waiting...")
            
            # Yield to environment, allowing other processes to run (and hopefully release memory)
            yield env.timeout(1.0) 

    # --- Hold Memory ---
    
    # Exponential distribution for memory holding time
    hold_time = np.random.exponential(AVG_HOLD_TIME)
    print(f"[{env.now:5.1f}] P-ID {pid} allocated {requested_size}KB. Holding for {hold_time:.2f} time units.")
    yield env.timeout(hold_time)

    # --- Release Memory ---
    
    mem_manager.release(pid)
    print(f"[{env.now:5.1f}] P-ID {pid} released {requested_size}KB.")
    
def arrival_generator(env, mem_manager):
    """Generates new process arrivals."""
    i = 0
    while i < MAX_PROCESSES:
        i += 1
        
        # Exponential distribution for inter-arrival time
        inter_arrival_time = np.random.exponential(AVG_ARRIVAL_TIME)
        yield env.timeout(inter_arrival_time)
        
        # Start a new process
        env.process(process_runner(env, mem_manager))
        
def monitor(env, mem_manager, interval=5.0):
    """Monitors memory usage and fragmentation at fixed time intervals."""
    # Updated column headers for clarity (Holes = Fragments)
    print("\n" + "="*90)
    print("TIME | Used (KB) | Free (KB) | Largest Free Block (KB) | # Holes (Fragments) | Failed Requests")
    print("="*90)
    
    while True:
        yield env.timeout(interval)
        stats = mem_manager.get_stats()
        
        # Format and log the statistics
        print(f"{env.now:4.1f} | {stats['used']:9d} | {stats['free']:9d} | {stats['largest_free']:23d} | {stats['num_free_blocks']:19d} | {stats['failed_allocations']:15d}")

# --- 4. SIMULATION EXECUTION ---

def run_simulation():
    # Set the random seeds for reproducibility
    random.seed(42)
    np.random.seed(42) 

    # 1. Initialize SimPy environment and Memory Manager
    env = simpy.Environment()
    mem_manager = MemoryManager(TOTAL_MEMORY_SIZE)

    # 2. Start the generator and monitor processes
    env.process(arrival_generator(env, mem_manager))
    env.process(monitor(env, mem_manager))

    # 3. Run the simulation (run until MAX_PROCESSES have arrived and had a chance to run)
    print(f"\n--- Starting Contiguous Memory Allocation Simulation ---")
    print(f"Total Memory: {TOTAL_MEMORY_SIZE}KB | Block Sizes: {BLOCK_SIZES}KB")
    
    # We will run for a generous fixed time to allow processes to complete
    env.run(until=150.0) 
    
    print("="*90)
    print("SIMULATION FINISHED.")
    final_stats = mem_manager.get_stats()
    print(f"Final Free Memory: {final_stats['free']}KB | Final Holes: {final_stats['num_free_blocks']} | Total Failed Requests: {final_stats['failed_allocations']}") # NEW: Report final failed count

if __name__ == '__main__':
    run_simulation()


import simpy
import time

# --- Configuration ---
GREEN_DURATION = 30  # seconds
YELLOW_DURATION = 5  # seconds
SIM_DURATION = 150   # seconds (about 2 full cycles)

def traffic_light_cycle(env):
    """
    SimPy process modeling a continuous 2-stage traffic light cycle.
    Stage 1: North/South (NS) movement allowed.
    Stage 2: East/West (EW) movement allowed.
    """
    print(f"[{env.now:5.1f}s] SIMULATION START: Traffic Light System Initialized.")

    while True:
        # --- Stage 1: North/South Green ---
        print(f"[{env.now:5.1f}s] Phase: NS (GREEN), EW (RED). Allowing traffic flow North/South.")
        yield env.timeout(GREEN_DURATION) # NS stays green for 30s

        # --- Stage 1 Transition: North/South Yellow ---
        print(f"[{env.now:5.1f}s] Phase: NS (YELLOW), EW (RED). Preparing to stop North/South traffic.")
        yield env.timeout(YELLOW_DURATION) # NS stays yellow for 5s

        # --- Stage 2: East/West Green ---
        print(f"[{env.now:5.1f}s] Phase: NS (RED), EW (GREEN). Allowing traffic flow East/West.")
        # Note: NS remains red for the entire EW green and yellow phases.
        yield env.timeout(GREEN_DURATION) # EW stays green for 30s

        # --- Stage 2 Transition: East/West Yellow ---
        print(f"[{env.now:5.1f}s] Phase: NS (RED), EW (YELLOW). Preparing to stop East/West traffic.")
        yield env.timeout(YELLOW_DURATION) # EW stays yellow for 5s

        # Cycle repeats. The total cycle duration is (30+5) + (30+5) = 70 seconds.

# --- Setup and Run the Simulation ---

# Initialize the SimPy environment
env = simpy.Environment()

# Start the traffic light process
env.process(traffic_light_cycle(env))

# Run the simulation for the defined duration
print(f"Running simulation for {SIM_DURATION} seconds...")
env.run(until=SIM_DURATION)

print(f"[{env.now:5.1f}s] SIMULATION END.")

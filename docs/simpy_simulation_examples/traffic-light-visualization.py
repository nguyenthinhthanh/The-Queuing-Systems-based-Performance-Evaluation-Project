import simpy
import time
import sys

# --- Configuration ---
GREEN_DURATION = 30  # seconds
YELLOW_DURATION = 5  # seconds
SIM_DURATION = 150   # seconds (about 2 full cycles)
# Real-world pause to make the "animation" visible in the console
PRINT_DELAY = 0.05   # seconds

# --- ANSI Color Codes for Visual Console Output ---
RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
CYAN = '\033[96m'
BOLD = '\033[1m'
RESET = '\033[0m'
# Note: These codes work in most modern terminals (like Linux, macOS, and newer Windows terminals).

def colorize(text, color):
    """Applies ANSI color codes to the text."""
    return f"{color}{text}{RESET}"

def log_status(env, ns_state, ew_state, message):
    """Logs the current state with time and colored visualization."""
    
    # 1. Colorize the light states
    def get_colored_state(state):
        if state == "GREEN":
            return colorize("GREEN", GREEN)
        elif state == "YELLOW":
            return colorize("YELLOW", YELLOW)
        else: # RED
            return colorize("RED", RED)

    ns_display = get_colored_state(ns_state).ljust(25) # Padding for alignment
    ew_display = get_colored_state(ew_state).ljust(25)
    
    # 2. Print the formatted line
    print(f"[{env.now:5.1f}s] | NS Light: {ns_display} | EW Light: {ew_display} | {message}")
    
    # 3. Add a real-world pause to make the event flow visible (the "animation")
    sys.stdout.flush()
    time.sleep(PRINT_DELAY)

def traffic_light_cycle(env):
    """
    SimPy process modeling a continuous 2-stage traffic light cycle.
    This demonstrates the event-based system: the process halts at 'yield' 
    and only resumes after the scheduled timeout event occurs.
    """
    print(colorize(f"\n[{env.now:5.1f}s] === Traffic Light System Initialized ===\n", BOLD + CYAN))

    while True:
        # --- Stage 1: North/South Green ---
        log_status(env, "GREEN", "RED", "NS traffic flows.")
        # Event 1: Pause process until 30 seconds have passed.
        yield env.timeout(GREEN_DURATION) 

        # --- Stage 1 Transition: North/South Yellow ---
        log_status(env, "YELLOW", "RED", "NS prepares to stop.")
        # Event 2: Pause process until 5 seconds have passed.
        yield env.timeout(YELLOW_DURATION) 

        # --- Stage 2: East/West Green ---
        # Note: NS remains RED for the next 35 seconds.
        log_status(env, "RED", "GREEN", "EW traffic flows.")
        # Event 3: Pause process until 30 seconds have passed.
        yield env.timeout(GREEN_DURATION) 

        # --- Stage 2 Transition: East/West Yellow ---
        log_status(env, "RED", "YELLOW", "EW prepares to stop.")
        # Event 4: Pause process until 5 seconds have passed.
        yield env.timeout(YELLOW_DURATION) 
        
        # Cycle repeats. Total cycle duration is 70 seconds.

# --- Setup and Run the Simulation ---

def run_simulation():
    # Initialize the SimPy environment
    env = simpy.Environment()

    print(colorize(f"Running simulation for {SIM_DURATION} simulated seconds.", BOLD))
    print("-" * 110)
    
    # Start the traffic light process
    env.process(traffic_light_cycle(env))

    # Run the environment until the specified time
    env.run(until=SIM_DURATION)
    
    print("-" * 110)
    print(colorize(f"[{env.now:5.1f}s] SIMULATION END. Total events processed.", BOLD + CYAN))

if __name__ == "__main__":
    run_simulation()


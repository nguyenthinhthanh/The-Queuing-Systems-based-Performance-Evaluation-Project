# The Queuing Systems-based Performance Evaluation Project

## Overview
This project implements **a queuing system simulator** using **SimPy** (a discrete-event simulation library in Python).  
It models **single and multiple-queue systems**, evaluates system performance, and provides a clear **mapping between real-world problems and queuing models** (e.g., M/M/1, M/M/c/K).

---

## Objectives
- Implement queuing systems using SimPy according to **Kendall’s notation**: `A / B / m / K / n / D`.
- Support multiple queue configurations:
  - `M/M/1`
  - `M/M/1/K`
  - `M/M/n`
  - `M/M/n/n` (Erlang-Loss)
  - `M/M/c/K`
- Design and simulate **multiple interconnected queuing systems** (series, parallel, or network).
- Analyze system performance through **simulation results** and compare them with **theoretical expectations**.
- Provide **mapping between real-world problems** and their **queuing system representations**.

---

## System Design
### Reference Architecture
The system consists of three key modules:

1. **Arrival Event**  
   - Generates arrivals following exponential or user-defined distributions.
2. **Server Module**  
   - Handles service processing, multiple servers, and queue capacity.
3. **Processing Event / Logger**  
   - Records events, queue status, waiting times, and performance metrics.

### Main Flow
```python
sys = MMcKSystem(arrival_rate, service_rate, servers, queue_size)
sys.run()
```

### Simulation Flow

Simulation proceeds by scheduling and processing events in time order:

- `init()` → generate arrivals  
- `arrival()` → handle new customers  
- `beginService()` → start service  
- `endService()` → release server  
- `schedule()` → manage event timeline  

---

### Mapping from Real-world to Queuing Model

| Real-world Scenario | Queuing Model | Description |
|----------------------|---------------|--------------|
| CPU Scheduling (Multi-level feedback) | M/M/c network | Multiple cores/processors act as servers |
| Multi-hop network routing | M/M/1 → M/M/c/K chain | Each router represents a queue node |
| Hospital or clinic system | M/M/c/K | Multiple doctors serving patients |
| Cloud service request handling | M/M/n | Each worker thread = a server |

---

### Experiments & Evaluation

- Simulation conducted under **two or more workloads**.  
- Validation against **Little’s Law** and analytical formulas.  
- Collected metrics:
  - Average waiting time  
  - Server utilization  
  - Queue length distribution  
  - Throughput and loss probability  

---

### Results

- Visualization of queue behavior under varying loads.  
- Comparison between **analytical** and **simulated** results.  
- Discussion of performance bottlenecks and scalability.  

---

### Technologies

- **Language:** Python 3.x  
- **Simulation Library:** [SimPy](https://simpy.readthedocs.io/)  
- **Visualization:** Matplotlib, Pandas  
- **Environment:** Jupyter Notebook  

---

### How to Run

**Install dependencies:**
```bash
pip install simpy matplotlib pandas jupyter
```

**Run the notebook:**
```bash
jupyter notebook Assignment1_SPE.ipynb
```
Modify parameters to simulate various queue models.

---

### References

- **SimPy Documentation:** [https://simpy.readthedocs.io/](https://simpy.readthedocs.io/)  
- **Kleinrock, L.** *Queueing Systems, Volume I: Theory*  

---

### License
This repository is for **educational and academic purposes only.** 


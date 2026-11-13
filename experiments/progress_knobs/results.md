### Transfer cost (sync phase, nothing else running on the rank)

| placement | transport | mode | timeout ms | sleep us | transfer s/step | GB/s (all ranks) | thread join ms | client bg CPU during transfer | client bg CPU s/GiB |
|---|---|---|---|---|---|---|---|---|---|
| shared | tcp | busy | 100 | 0 | 0.096 | 11.2 | 38.0 | 85% | 0.66 |
| shared | tcp | busy | 100 | 1000 | 0.083 | 12.9 | 43.2 | 85% | 0.63 |
| shared | tcp | busy | 100 | 10000 | 0.101 | 10.7 | 49.3 | 76% | 0.64 |
| shared | tcp | busy | 10 | 1000 | 0.106 | 10.1 | 5.3 | 74% | 0.35 |
| shared | tcp | busy | 1 | 1000 | 0.120 | 9.0 | 1.2 | 60% | 0.29 |
| shared | tcp | busy | 0 | 100 | 0.091 | 11.8 | 0.2 | 50% | 0.18 |
| shared | tcp | busy | 0 | 1000 | 0.136 | 7.9 | 0.6 | 40% | 0.22 |
| shared | tcp | busy | 0 | 10000 | 0.241 | 4.5 | 6.5 | 18% | 0.20 |
| shared | tcp | blocking | 100 | 1000 | 0.085 | 12.7 | 48.4 | 85% | 0.63 |
| shared | sockets | busy | 100 | 1000 | 0.168 | 6.4 | 48.9 | 82% | 0.87 |
| shared | sockets | blocking | 100 | 1000 | 0.144 | 7.5 | 61.7 | 46% | 0.45 |
| smt | tcp | busy | 100 | 1000 | 0.104 | 10.4 | 56.0 | 94% | 0.76 |
| smt | tcp | busy | 0 | 1000 | 0.128 | 8.4 | 0.7 | 49% | 0.25 |
| smt | sockets | blocking | 100 | 1000 | 0.137 | 7.8 | 49.7 | 56% | 0.55 |

### Client background CPU over the async run (sleep kernel)

| placement | transport | mode | timeout ms | sleep us | progress thread | all non-main threads | threads |
|---|---|---|---|---|---|---|---|
| shared | tcp | busy | 100 | 0 | 92% | 92% | 4 |
| shared | tcp | busy | 100 | 1000 | 90% | 90% | 4 |
| shared | tcp | busy | 100 | 10000 | 83% | 83% | 4 |
| shared | tcp | busy | 10 | 1000 | 86% | 86% | 4 |
| shared | tcp | busy | 1 | 1000 | 69% | 69% | 4 |
| shared | tcp | busy | 0 | 100 | 39% | 39% | 4 |
| shared | tcp | busy | 0 | 1000 | 34% | 34% | 4 |
| shared | tcp | busy | 0 | 10000 | 12% | 12% | 4 |
| shared | tcp | blocking | 100 | 1000 | 92% | 92% | 4 |
| shared | sockets | busy | 100 | 1000 | 94% | 94% | 6 |
| shared | sockets | blocking | 100 | 1000 | 0% | 60% | 7 |
| smt | tcp | busy | 100 | 1000 | 92% | 92% | 4 |
| smt | tcp | busy | 0 | 1000 | 33% | 33% | 4 |
| smt | sockets | blocking | 100 | 1000 | 0% | 58% | 7 |

### Transfer rate while the kernel runs (async phase), GB/s

| placement | transport | mode | timeout ms | sleep us | spin | fma | stream |
|---|---|---|---|---|---|---|---|
| shared | tcp | busy | 100 | 0 | 9.1 | 9.4 | 7.9 |
| shared | tcp | busy | 100 | 1000 | 7.0 | 8.0 | 6.3 |
| shared | tcp | busy | 100 | 10000 | 6.4 | 6.7 | 5.5 |
| shared | tcp | busy | 10 | 1000 | 7.3 | 6.9 | 6.7 |
| shared | tcp | busy | 1 | 1000 | 7.5 | 6.5 | 6.3 |
| shared | tcp | busy | 0 | 100 | 6.8 | 7.4 | 5.7 |
| shared | tcp | busy | 0 | 1000 | 6.5 | 7.8 | 6.3 |
| shared | tcp | busy | 0 | 10000 | 5.2 | 4.4 | 3.9 |
| shared | tcp | blocking | 100 | 1000 | 7.5 | 7.6 | 6.3 |
| shared | sockets | busy | 100 | 1000 | 7.0 | 8.1 | 6.4 |
| shared | sockets | blocking | 100 | 1000 | 7.6 | 9.1 | 6.6 |
| smt | tcp | busy | 100 | 1000 | 9.6 | 10.6 | 7.9 |
| smt | tcp | busy | 0 | 1000 | 8.4 | 6.8 | 5.2 |
| smt | sockets | blocking | 100 | 1000 | 7.7 | 6.5 | 4.9 |

### Compute slowdown (t_comp / t_alone)

| placement | transport | mode | timeout ms | sleep us | spin | fma | stream |
|---|---|---|---|---|---|---|---|
| shared | tcp | busy | 100 | 0 | 2.24x | 1.93x | 1.80x |
| shared | tcp | busy | 100 | 1000 | 2.21x | 1.93x | 1.81x |
| shared | tcp | busy | 100 | 10000 | 1.99x | 1.84x | 1.75x |
| shared | tcp | busy | 10 | 1000 | 2.03x | 1.88x | 1.77x |
| shared | tcp | busy | 1 | 1000 | 1.96x | 1.82x | 1.82x |
| shared | tcp | busy | 0 | 100 | 1.51x | 1.32x | 1.56x |
| shared | tcp | busy | 0 | 1000 | 1.47x | 1.37x | 1.55x |
| shared | tcp | busy | 0 | 10000 | 1.16x | 1.16x | 1.22x |
| shared | tcp | blocking | 100 | 1000 | 2.17x | 1.91x | 1.78x |
| shared | sockets | busy | 100 | 1000 | 2.13x | 1.87x | 1.84x |
| shared | sockets | blocking | 100 | 1000 | 1.43x | 1.26x | 1.35x |
| smt | tcp | busy | 100 | 1000 | 1.25x | 1.31x | 1.35x |
| smt | tcp | busy | 0 | 1000 | 1.22x | 1.21x | 1.43x |
| smt | sockets | blocking | 100 | 1000 | 1.23x | 1.24x | 1.43x |

### Exposed time per step after compute (wait + join), ms

| placement | transport | mode | timeout ms | sleep us | sleep | spin | fma | stream |
|---|---|---|---|---|---|---|---|---|
| shared | tcp | busy | 100 | 0 | 38 | 47 | 45 | 50 |
| shared | tcp | busy | 100 | 1000 | 43 | 45 | 46 | 36 |
| shared | tcp | busy | 100 | 10000 | 49 | 53 | 53 | 36 |
| shared | tcp | busy | 10 | 1000 | 5 | 2 | 3 | 3 |
| shared | tcp | busy | 1 | 1000 | 1 | 1 | 1 | 1 |
| shared | tcp | busy | 0 | 100 | 0 | 0 | 0 | 0 |
| shared | tcp | busy | 0 | 1000 | 1 | 1 | 1 | 1 |
| shared | tcp | busy | 0 | 10000 | 8 | 6 | 5 | 5 |
| shared | tcp | blocking | 100 | 1000 | 48 | 55 | 60 | 44 |
| shared | sockets | busy | 100 | 1000 | 49 | 41 | 52 | 39 |
| shared | sockets | blocking | 100 | 1000 | 62 | 56 | 54 | 73 |
| smt | tcp | busy | 100 | 1000 | 56 | 53 | 56 | 64 |
| smt | tcp | busy | 0 | 1000 | 1 | 1 | 1 | 1 |
| smt | sockets | blocking | 100 | 1000 | 50 | 40 | 40 | 57 |

### Run time vs. sleep prediction of the same config

| placement | transport | mode | timeout ms | sleep us | spin | fma | stream |
|---|---|---|---|---|---|---|---|
| shared | tcp | busy | 100 | 0 | +101% | +84% | +77% |
| shared | tcp | busy | 100 | 1000 | +79% | +67% | +60% |
| shared | tcp | busy | 100 | 10000 | +83% | +74% | +57% |
| shared | tcp | busy | 10 | 1000 | +101% | +85% | +74% |
| shared | tcp | busy | 1 | 1000 | +105% | +91% | +88% |
| shared | tcp | busy | 0 | 100 | +67% | +51% | +62% |
| shared | tcp | busy | 0 | 1000 | +54% | +48% | +64% |
| shared | tcp | busy | 0 | 10000 | +19% | +29% | +26% |
| shared | tcp | blocking | 100 | 1000 | +83% | +70% | +60% |
| shared | sockets | busy | 100 | 1000 | +98% | +96% | +75% |
| shared | sockets | blocking | 100 | 1000 | +43% | +31% | +39% |
| smt | tcp | busy | 100 | 1000 | +12% | +25% | +34% |
| smt | tcp | busy | 0 | 1000 | +30% | +17% | +38% |
| smt | sockets | blocking | 100 | 1000 | +19% | +22% | +45% |

4 clients x 256 MiB per step, 5 steps, median of 3 runs. Compute per step = 1.25 x transfer time. Server busy-polls on its own core.

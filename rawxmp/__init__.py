"""rawxmp — deterministic, explainable CR2 -> Camera Raw XMP generator."""
import os

# Our linear algebra is 3x3 matrices: extra BLAS threads only cost memory (each worker process would otherwise
# allocate buffers for every CPU core -> "OpenBLAS error: Memory allocation still failed").  Must precede numpy import.
for _v in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

__version__ = "0.1.0"

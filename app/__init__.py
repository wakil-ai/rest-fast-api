import warnings

warnings.filterwarnings(
    "ignore", category=DeprecationWarning, message="'audioop' is deprecated"
)
warnings.filterwarnings(
    "ignore",
    message="None of PyTorch, TensorFlow >= 2.0, or Flax have been found",
)

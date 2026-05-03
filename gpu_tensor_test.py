import torch
import time

print("=" * 50)
print("🚀 GPU TENSOR TEST")
print("=" * 50)

# Check CUDA availability
print("CUDA Available:", torch.cuda.is_available())

if not torch.cuda.is_available():
    print("❌ CUDA not available")
    exit()

# Device info
device = torch.device("cuda")
print("Using Device:", torch.cuda.get_device_name(0))

# Create large tensors
size = 5000  # increase for stress test
print(f"\nCreating {size}x{size} tensors...")

start = time.time()

a = torch.randn(size, size, device=device)
b = torch.randn(size, size, device=device)

print("✅ Tensors created on GPU")

# Perform computation
print("\nPerforming matrix multiplication on GPU...")
start_compute = time.time()

c = torch.matmul(a, b)

# Force computation
torch.cuda.synchronize()

end_compute = time.time()

print("✅ Computation done")

# Memory usage
allocated = torch.cuda.memory_allocated() / (1024**2)
reserved = torch.cuda.memory_reserved() / (1024**2)

print(f"\n📊 GPU Memory Allocated: {allocated:.2f} MB")
print(f"📊 GPU Memory Reserved: {reserved:.2f} MB")

print(f"\n⏱️ Compute Time: {end_compute - start_compute:.2f} sec")

print("\n🔥 GPU is actively working!")
print("=" * 50)
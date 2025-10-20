#!/usr/bin/env python3
"""
Merge and plot log metrics with NERSC job counts by timestamp
Requires pandas. Run: pyenv ana

Usage:
    python3 utils/plot_logs.py <log_csv> <nersc_csv>
    
Example:
    python3 utils/plot_logs.py log.mu2e.PiBeam.MDC2025ac.csv data/nersc_runjobs.csv
"""
import pandas as pd # type: ignore
import matplotlib.pyplot as plt # type: ignore
import matplotlib.dates as mdates # type: ignore
import sys
import os

if len(sys.argv) != 3:
    print("Usage: plot_logs.py <log_csv> <nersc_csv>")
    print("Example: plot_logs.py log.mu2e.PiBeam.MDC2025ac.csv data/nersc_runjobs.csv")
    sys.exit(1)

log_file = sys.argv[1]
nersc_file = sys.argv[2]

# Load log metrics
log_df = pd.read_csv(log_file)
log_df['datetime'] = pd.to_datetime(log_df['date'].str.rsplit(' ', n=1).str[0], format='%d-%b-%Y %H:%M:%S')
log_df = log_df.sort_values('datetime')

# Load NERSC job counts
nersc_df = pd.read_csv(nersc_file)
nersc_df['datetime'] = pd.to_datetime(nersc_df['Time'])
nersc_df = nersc_df.sort_values('datetime')
job_col = nersc_df.columns[1]
nersc_df[job_col] = pd.to_numeric(nersc_df[job_col], errors='coerce')
nersc_df = nersc_df.dropna(subset=[job_col])

print(f"Log data: {len(log_df)} points from {log_df['datetime'].min()} to {log_df['datetime'].max()}")
print(f"NERSC data: {len(nersc_df)} points from {nersc_df['datetime'].min()} to {nersc_df['datetime'].max()}")

# Merge on nearest timestamp (within 30 minutes)
df = pd.merge_asof(
    log_df.sort_values('datetime'),
    nersc_df[['datetime', job_col]].sort_values('datetime'),
    on='datetime',
    direction='nearest',
    tolerance=pd.Timedelta('30min')
)
df = df.dropna(subset=[job_col])

if len(df) == 0:
    print("Error: No overlapping timestamps found!")
    sys.exit(1)

print(f"Merged: {len(df)} points\n")

# Create figure with 3 subplots
fig, axes = plt.subplots(3, 1, figsize=(12, 10))
fig.suptitle(os.path.basename(log_file).replace('.csv', '.log'), fontsize=14, fontweight='bold')

# Plot 1: Running jobs
axes[0].scatter(df['datetime'], df[job_col], s=20, alpha=0.7, color='C0')
axes[0].set_ylabel('Running Jobs', fontsize=11)
axes[0].grid(alpha=0.3)
axes[0].tick_params(labelbottom=False)  # Hide x-axis labels

# Plot 2: CPU/Real time
cpu_mean = df['CPU [h]'].mean()
real_mean = df['Real [h]'].mean()
axes[1].scatter(df['datetime'], df['CPU [h]'], s=20, label=f'CPU (μ={cpu_mean:.2f})', alpha=0.7)
axes[1].scatter(df['datetime'], df['Real [h]'], s=20, label=f'Real (μ={real_mean:.2f})', alpha=0.7)
axes[1].set_ylabel('Time [h]', fontsize=11)
axes[1].legend()
axes[1].grid(alpha=0.3)
axes[1].tick_params(labelbottom=False)  # Hide x-axis labels

# Plot 3: Memory
vmpeak_mean = df['VmPeak [GB]'].mean()
vmhwm_mean = df['VmHWM [GB]'].mean()
axes[2].scatter(df['datetime'], df['VmPeak [GB]'], s=20, label=f'VmPeak (μ={vmpeak_mean:.2f})', alpha=0.7)
axes[2].scatter(df['datetime'], df['VmHWM [GB]'], s=20, label=f'VmHWM (μ={vmhwm_mean:.2f})', alpha=0.7)
axes[2].set_ylabel('Memory [GB]', fontsize=11)
axes[2].legend()
axes[2].grid(alpha=0.3)
axes[2].xaxis.set_major_formatter(mdates.DateFormatter('%b-%d %H:%M'))
axes[2].tick_params(axis='x', rotation=45)

plt.tight_layout(rect=[0, 0, 1, 0.97])

# Save output
output = log_file.replace('.csv', '.png')
plt.savefig(output, dpi=150)
print(f'Saved: {output}')

# Stats
print(f'\nFiles: {len(df)}')
print(f'CPU:  {df["CPU [h]"].mean():.2f} ± {df["CPU [h]"].std():.2f} h')
print(f'Real: {df["Real [h]"].mean():.2f} ± {df["Real [h]"].std():.2f} h')
print(f'Mem:  {df["VmPeak [GB]"].mean():.2f} ± {df["VmPeak [GB]"].std():.2f} GB')
print(f'\nCorrelations with {job_col}:')
print(f'  CPU [h]:      {df[job_col].corr(df["CPU [h]"]):.3f}')
print(f'  Real [h]:     {df[job_col].corr(df["Real [h]"]):.3f}')
print(f'  VmPeak [GB]:  {df[job_col].corr(df["VmPeak [GB]"]):.3f}')
print(f'  VmHWM [GB]:   {df[job_col].corr(df["VmHWM [GB]"]):.3f}')

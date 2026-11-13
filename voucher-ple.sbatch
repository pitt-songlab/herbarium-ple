#!/bin/bash

#SBATCH --cluster=htc
#SBATCH --partition=htc
#SBATCH --output="voucher-ple.log"
#SBATCH --job-name="voucher-ple"
#SBATCH --time=36:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=8G

module load anaconda3
cd /ihome/ysong/yis218/GitHub/herbarium-ple
source activate ./envs
python -u run.py

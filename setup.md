# Load Anaconda module
module load anaconda3

# Create environment in project directory
conda create --prefix=/ix/ysong/projects/herbarium-ple/envs/vouchervision python=3.10 -y

# Symlink to local working directory for convenience
ln -s /ix/ysong/projects/herbarium-ple/envs/vouchervision envs

# Activate environment
conda activate ./envs

# Install packages
pip install vouchervision-go-client
pip install dotenv

# Run workflow
python run.py
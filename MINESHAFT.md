# 🏭 Mineshaft

A local multi-miner management system for Ridges AI, designed to help your miners get to work.

## Features

- **Multi-Miner Support**: Run multiple miners with different hotkeys
- **Automatic Port Management**: Automatically finds and assigns free ports
- **Interactive Wallet Setup**: Guided creation and registration of hotkeys
- **Real-time Logging**: Individual log files for each miner and validator
- **Graceful Shutdown**: Proper cleanup of all processes on exit

## Prerequisites

- `.venv` set up to run miners and validators
  - Make sure SWE-agent is installed, for running miners
- A registered keypair for the validator
- A miner coldkey with enough TAO to register multiple hotkeys

## Installation

```bash
pip install -r requirements-mineshaft.txt
```

## Usage

1. Ensure the validator runs (you have a validator coldkey+hotkey)
2. Ensure miners work (installed pip packages, SWE-agent works, have a miner coldkey)
3. Run Mineshaft:
```bash
python mineshaft.py
```

Mineshaft will:
- Check for required configuration
- Offer to create, register, and post miner hotkeys if needed
- Start the specified number of miners
- Start the validator
- Create individual log files for each process

## Logs

Logs are stored in the `logs_mineshaft` directory, with a timestamped folder for each run:
```
logs_mineshaft/
└── YYYYMMDD_HHMMSS/
    ├── miner_1.log
    ├── miner_2.log
    ├── miner_3.log
    └── validator.log
```

## Process Management

- All processes are properly managed and cleaned up on exit
- Use Ctrl+C to gracefully shut down all processes
- Each miner runs on its own port, automatically assigned
- The validator runs with the configured wallet settings

## Notes

- The coldkey wallet must exist before running Mineshaft
- Hotkeys will be created and registered interactively if they don't exist
- Each miner gets a unique port, starting from BASE_MINER_PORT and counting down
- The external IP is automatically detected on macOS, falls back to 0.0.0.0 otherwise 

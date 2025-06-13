# 🏭 Mineshaft - One-Click Multi-miner Management

**Get your miners to work since 2024!**

Mineshaft is a comprehensive automation tool for running multiple Ridges AI miners and a validator simultaneously. It handles wallet creation, fauceting, registration, IP posting, and process management with beautiful terminal UI.

## ✨ Features

- **🎯 One-Click Operation**: Start multiple miners and validator with a single command
- **🧠 Smart Wallet Management**: Uses single coldkey with multiple hotkeys (mineshaft-1, mineshaft-2, etc.)
- **♻️ Hotkey Reuse**: Reuses existing hotkeys to avoid recreating wallets
- **💰 Intelligent Fauceting**: Checks coldkey balance and faucets only when needed
- **🔄 API Key Distribution**: Evenly distributes API keys among miners
- **📊 Real-time Monitoring**: Beautiful progress bars and status displays
- **🛡️ Graceful Shutdown**: Clean process termination with Ctrl+C
- **⚙️ Configuration-Based**: Always reads from mineshaft.env file
- **🎯 Ultra Simple**: Just one command to run everything

## 🚀 Quick Start

### Step 1: Install Dependencies
```bash
pip install -r requirements-mineshaft.txt
```

### Step 2: Run
```bash
python mineshaft.py
```
This will prompt for any missing configuration and save it to `mineshaft.env`.

That's it! Mineshaft will:
1. ✅ Check/create your miner coldkey
2. ✅ Check balance and faucet if needed (testnet)
3. ✅ Create hotkeys `mineshaft-1`, `mineshaft-2`, etc. (or reuse existing)
4. ✅ Register all hotkeys on the subnet
5. ✅ Post IP addresses
6. ✅ Start all miners and validator

## 📋 Configuration Options

All configuration is stored in `mineshaft.env`. Missing values will be prompted for interactively.

### Basic Settings
- `SUBTENSOR_ADDRESS`: WebSocket address (default: ws://127.0.0.1:9945)
- `NETUID`: Subnet ID (default: 1)
- `NUM_MINERS`: Number of miners to run (default: 3)
- `DATABASE_PATH`: Validator database path (default: validator.db)

### Wallet Settings
- `MINER_COLDKEY_NAME`: Coldkey name for all miners (default: miner)
- `VALIDATOR_WALLET_NAME`: Validator wallet name (default: validator)
- `VALIDATOR_HOTKEY_NAME`: Validator hotkey name (default: default)

### API Configuration
- `API_KEYS`: Comma-separated API keys for AI services (required)

### Advanced Settings
- `BASE_MINER_PORT`: Starting port for miners (default: 7999, counts down)
- `EXTERNAL_IP`: External IP for fiber-post-ip (default: 0.0.0.1)
- `CREATE_NEW_DATABASE`: Delete existing database on start (default: false)
- `LOG_LEVEL`: Logging level (default: INFO)

## 🎮 Usage

### Start Mining
```bash
# Default - prompts for missing config and starts everything
python mineshaft.py

# Or explicitly use the run command
python mineshaft.py run
```

**Stop**: Use `Ctrl+C` to gracefully stop all processes

**Status**: Process status is shown while running

## 🏗️ How It Works

### Wallet Architecture
- **Single Coldkey**: All miners use one coldkey (e.g., "miner")
- **Multiple Hotkeys**: Each miner gets a unique hotkey:
  - `miner:mineshaft-1` (port 7999)
  - `miner:mineshaft-2` (port 7998)
  - `miner:mineshaft-3` (port 7997)
  - etc.
- **Hotkey Reuse**: Existing hotkeys are reused to avoid recreation
- **Smart Fauceting**: Only faucets coldkey when balance is insufficient

### Process Flow
1. **📝 Configuration**: Reads `mineshaft.env`, prompts for missing values
2. **💳 Coldkey Setup**: 
   - Checks/creates miner coldkey
   - Checks balance, faucets if needed (testnet only)
3. **🔑 Hotkey Setup**: For each miner:
   - Checks if hotkey exists, reuses or creates new
   - Registers hotkey on subnet
   - Posts IP address
4. **🛡️ Validator Setup**: Creates separate validator wallet
5. **⛏️ Process Start**: Starts miners, then validator
6. **👀 Monitoring**: Monitors all processes with graceful cleanup

## 🔧 Requirements

- **Python 3.8+** with `pip install -r requirements-mineshaft.txt`
- **btcli**: Bittensor CLI tool
- **fiber-post-ip**: Fiber IP posting utility
- **uvicorn**: For running miners
- **uv**: For running validator

## 📊 What You'll See

Mineshaft provides beautiful terminal output with:
- 🎨 **Rich progress indicators** for each setup step
- 📊 **Real-time status tables** showing all components
- 🎯 **Color-coded status messages** for easy monitoring
- 💳 **Balance checking** and fauceting notifications

## 💡 Pro Tips

1. **Reuse Hotkeys**: Mineshaft automatically reuses existing hotkeys - no need to delete them
2. **Monitor Balance**: Check your coldkey balance before large deployments
3. **Start Small**: Begin with 2-3 miners to test your setup
4. **API Key Strategy**: Distribute keys evenly for best performance
5. **Testnet First**: Always test on testnet before mainnet
6. **Graceful Shutdown**: Use Ctrl+C for clean process termination

## 🛠️ Troubleshooting

### Wallet Issues
- Ensure `btcli` is installed and accessible
- Check your coldkey exists and has sufficient balance
- Verify hotkey creation succeeded

### Balance Issues
- On testnet: Mineshaft will faucet automatically
- On mainnet: Ensure sufficient TAO in your coldkey for registrations

### Network Issues
- Confirm subtensor endpoint is accessible
- Check firewall settings for miner ports
- Verify external IP configuration

### Process Issues
- Monitor system resources (CPU, memory)
- Check for port conflicts
- Review process logs for specific errors

## 🎯 Example Workflows

### Development Setup (Testnet)
```bash
python mineshaft.py
# Will prompt: ws://127.0.0.1:9945, netuid=1, coldkey=miner, 3 miners, API keys
```

### Production Setup (Mainnet)
```bash
python mineshaft.py
# Will prompt: wss://entrypoint-finney.opentensor.ai:443, netuid=1, coldkey=mainnet-miner, 5 miners, API keys
```

### Quick Testing
```bash
python mineshaft.py  # Prompts for everything, then runs
```

## 📁 File Structure

After running, you'll have:
```
mineshaft.env           # Your configuration
validator.db           # Validator database (if not using custom path)
~/.bittensor/wallets/  # Your wallets:
  ├── miner/           # Miner coldkey
  │   ├── coldkey
  │   ├── hotkeys/
  │   │   ├── mineshaft-1
  │   │   ├── mineshaft-2
  │   │   └── mineshaft-3
  └── validator/       # Validator wallet
      ├── coldkey
      └── hotkeys/
          └── default
```

---

**Ready to get your miners to work? Let's dig! ⛏️** 
# Runpod Auto Pod Watcher

This tool waits until your requested Runpod GPU becomes available, then creates a new Pod using your selected template, network volume, and temporary container disk size.

## What it does

- Prompts you for the GPU family you want:
  - `RTX 2000 Ada`
  - `RTX 5090`
  - `RTX 4090`
  - `RTX PRO 6000 Server Edition`
  - `RTX PRO 6000 Workstation Edition`
  - `RTX PRO 6000 Any`
- Prompts you for temporary `containerDiskInGb`
- Lets you choose a Runpod template from your account
- Lets you choose a network volume from your account
- Polls Runpod until the GPU is available
- Creates a new Pod with the chosen settings
- Sends a Telegram notification after successful Pod creation if Telegram is configured
- Includes the Pod proxy URL and Runpod console URL in Telegram notifications
- Can optionally open the Pod proxy URL in your browser after creation

## Files

- `runpod_auto_pod_watcher.py`: main script
- `run_watcher.bat`: Windows launcher
- `config.json`: saved after first setup
- `state.json`: remembers the last created Pod and last container disk size
- `watcher.log`: polling and creation log

## First run

Run:

```bat
D:\Scripts\runpod_auto_pod_watcher\run_watcher.bat
```

On first launch the script will ask for:

1. Runpod API key
2. Cloud type (`COMMUNITY` or `SECURE`)
3. Poll interval
4. Mount path for the network volume
5. Pod name prefix
6. Optional preferred data centers
7. Template selection from your Runpod account
8. Network volume selection from your Runpod account
9. Optional Telegram bot token and chat ID

If you leave preferred data centers empty, the script uses the selected network volume data center automatically.

Then it asks for:

1. The GPU you want
2. The temporary container disk size in GB

## Reconfigure later

```bat
D:\Scripts\runpod_auto_pod_watcher\run_watcher.bat configure
```

## Show template and volume IDs

```bat
D:\Scripts\runpod_auto_pod_watcher\run_watcher.bat list
```

The template ID is printed as `id:` under each template. The network volume ID is printed as `id:` under each network volume.

## Telegram notifications

During configuration, enter:

- `Telegram bot token`: token from BotFather, for example `123456:ABC...`
- `Telegram chat ID`: your chat ID, group ID, or channel ID

Leave both empty to disable Telegram.

Test Telegram without creating a Pod:

```bat
D:\Scripts\runpod_auto_pod_watcher\run_watcher.bat telegram-config
```

```bat
D:\Scripts\runpod_auto_pod_watcher\run_watcher.bat telegram-test
```

## Pod links

By default, the Telegram message includes:

- `Pod URL`: `https://<pod-id>-8188.proxy.runpod.net`
- `Runpod console`: `https://www.runpod.io/console/pods/<pod-id>`

Change `pod_proxy_port` in `config.json` if your template exposes a different port.

Set this in `config.json` to open the Pod URL in your browser after creation:

```json
"open_pod_url_in_browser": true
```

## Notes

- The script uses the official Runpod GraphQL API to check GPU availability and the REST API to create Pods.
- Some Runpod availability responses report `stockStatus` without `availableGpuCounts`; in that case the watcher treats a non-empty stock status as worth trying.
- For `RTX PRO 6000 Any`, the watcher checks `Server Edition` first and then `Workstation Edition`.
- The tool attaches your selected `networkVolumeId` and uses your selected `templateId`.
- Container disk remains temporary and is erased when the Pod is reset or terminated.
- If you attach a network volume, building a fresh Pod when hardware appears is usually the cleaner workflow than waiting on one old Pod.

## Don@tes

**If any of this turns out to be useful for you - I'm glad.  
And if you feel like supporting it:  
1-2 coffees are more than enough.**

[Click to Buy me a Coffee](https://buymeacoffee.com/natlrazfx)

[Subscribe me on Substack](https://substack.com/@natalia289425)

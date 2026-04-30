#!/usr/bin/env python3
import json
import getpass
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, parse, request


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
LOG_PATH = BASE_DIR / "watcher.log"

GRAPHQL_URL = "https://api.runpod.io/graphql"
REST_URL = "https://rest.runpod.io/v1"
TELEGRAM_API_URL = "https://api.telegram.org"
HTTP_HEADERS = {
    "User-Agent": "runpod-auto-pod-watcher/1.1",
    "Accept": "application/json",
}


class RunpodHttpError(RuntimeError):
    def __init__(self, api_name: str, status_code: int, body: str):
        self.api_name = api_name
        self.status_code = status_code
        self.body = body
        super().__init__(f"{api_name} HTTP {status_code}: {body}")


GPU_OPTIONS = [
    {
        "key": "rtx2000ada",
        "label": "RTX 2000 Ada",
        "gpu_type_ids": ["NVIDIA RTX 2000 Ada Generation"],
    },
    {
        "key": "rtx5090",
        "label": "RTX 5090",
        "gpu_type_ids": ["NVIDIA GeForce RTX 5090"],
    },
    {
        "key": "rtx4090",
        "label": "RTX 4090",
        "gpu_type_ids": ["NVIDIA GeForce RTX 4090"],
    },
    {
        "key": "rtx6000pro_server",
        "label": "RTX PRO 6000 Server Edition",
        "gpu_type_ids": ["NVIDIA RTX PRO 6000 Blackwell Server Edition"],
    },
    {
        "key": "rtx6000pro_wk",
        "label": "RTX PRO 6000 Workstation Edition",
        "gpu_type_ids": ["NVIDIA RTX PRO 6000 Blackwell Workstation Edition"],
    },
    {
        "key": "rtx6000pro_any",
        "label": "RTX PRO 6000 Any",
        "gpu_type_ids": [
            "NVIDIA RTX PRO 6000 Blackwell Server Edition",
            "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
        ],
    },
]


DEFAULT_CONFIG = {
    "api_key": "",
    "cloud_type": "COMMUNITY",
    "poll_interval_seconds": 120,
    "volume_mount_path": "/workspace",
    "pod_name_prefix": "comfyui-auto",
    "data_center_ids": [],
    "template_id": "",
    "template_name": "",
    "network_volume_id": "",
    "network_volume_name": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
}


@dataclass
class TemplateChoice:
    template_id: str
    name: str
    image_name: str
    ports: List[str]


@dataclass
class NetworkVolumeChoice:
    volume_id: str
    name: str
    data_center_id: str
    size: Optional[int]


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def prompt_text(label: str, default: Optional[str] = None, secret: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        if secret:
            value = getpass.getpass(f"{label}{suffix}: ").strip()
        else:
            value = input(f"{label}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value:
            if secret:
                return value
            return value
        print("Value is required.")


def prompt_optional(label: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    if value:
        return value
    return default or ""


def prompt_optional_secret(label: str, default: Optional[str] = None) -> str:
    masked_default = "***" if default else None
    suffix = f" [{masked_default}]" if masked_default else ""
    value = getpass.getpass(f"{label}{suffix}: ").strip()
    if value:
        return value
    return default or ""


def prompt_int(label: str, default: int, minimum: int = 1) -> int:
    while True:
        raw_value = input(f"{label} [{default}]: ").strip()
        if not raw_value:
            return default
        try:
            value = int(raw_value)
        except ValueError:
            print("Enter an integer.")
            continue
        if value < minimum:
            print(f"Enter a value >= {minimum}.")
            continue
        return value


def prompt_choice(label: str, options: List[Dict[str, Any]], default_index: int = 1) -> Dict[str, Any]:
    print(label)
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option['label']}")
    while True:
        raw_value = input(f"Select option [{default_index}]: ").strip()
        if not raw_value:
            selected = default_index
        else:
            try:
                selected = int(raw_value)
            except ValueError:
                print("Enter a number from the list.")
                continue
        if 1 <= selected <= len(options):
            return options[selected - 1]
        print("Option out of range.")


class RunpodClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        if not self.api_key:
            raise ValueError("Runpod API key is required.")

    def _graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        url = f"{GRAPHQL_URL}?api_key={parse.quote(self.api_key)}"
        headers = {**HTTP_HEADERS, "Content-Type": "application/json"}
        req = request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RunpodHttpError("GraphQL", exc.code, body) from exc
        except error.URLError as exc:
            raise RuntimeError(f"GraphQL connection error: {exc}") from exc
        data = json.loads(body)
        if data.get("errors"):
            raise RuntimeError(f"GraphQL error: {data['errors']}")
        return data["data"]

    def _rest(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None
        headers = {**HTTP_HEADERS, "Authorization": f"Bearer {self.api_key}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        req = request.Request(f"{REST_URL}{path}", data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RunpodHttpError("REST", exc.code, body) from exc
        except error.URLError as exc:
            raise RuntimeError(f"REST connection error: {exc}") from exc
        if not body:
            return {}
        return json.loads(body)

    def list_templates(self) -> List[TemplateChoice]:
        payload = self._rest("GET", "/templates")
        items = payload if isinstance(payload, list) else payload.get("items", payload.get("data", []))
        templates: List[TemplateChoice] = []
        for item in items:
            templates.append(
                TemplateChoice(
                    template_id=item.get("id", ""),
                    name=item.get("name", "(unnamed template)"),
                    image_name=item.get("imageName", ""),
                    ports=item.get("ports", []) or [],
                )
            )
        return templates

    def list_network_volumes(self) -> List[NetworkVolumeChoice]:
        payload = self._rest("GET", "/networkvolumes")
        items = payload if isinstance(payload, list) else payload.get("items", payload.get("data", []))
        volumes: List[NetworkVolumeChoice] = []
        for item in items:
            volumes.append(
                NetworkVolumeChoice(
                    volume_id=item.get("id", ""),
                    name=item.get("name", "(unnamed volume)"),
                    data_center_id=item.get("dataCenterId", ""),
                    size=item.get("size"),
                )
            )
        return volumes

    def list_pods(self) -> List[Dict[str, Any]]:
        payload = self._rest("GET", "/pods")
        return payload if isinstance(payload, list) else payload.get("items", payload.get("data", []))

    def check_gpu_availability(self, gpu_type_id: str, secure_cloud: bool) -> Dict[str, Any]:
        query = """
        query CheckGpuAvailability($gpuTypeId: String!, $gpuCount: Int!, $secureCloud: Boolean!) {
          gpuTypes(input: { id: $gpuTypeId }) {
            id
            displayName
            lowestPrice(input: { gpuCount: $gpuCount, secureCloud: $secureCloud }) {
              stockStatus
              minimumBidPrice
              uninterruptablePrice
              availableGpuCounts
            }
          }
        }
        """
        data = self._graphql(
            query,
            {
                "gpuTypeId": gpu_type_id,
                "gpuCount": 1,
                "secureCloud": secure_cloud,
            },
        )
        gpu_types = data.get("gpuTypes", [])
        if not gpu_types:
            return {"id": gpu_type_id, "displayName": gpu_type_id, "lowestPrice": None}
        return gpu_types[0]

    def create_pod(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._rest("POST", "/pods", payload)


def send_telegram_message(config: Dict[str, Any], text: str) -> bool:
    bot_token = (config.get("telegram_bot_token") or "").strip()
    chat_id = (config.get("telegram_chat_id") or "").strip()
    if not bot_token or not chat_id:
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {**HTTP_HEADERS, "Content-Type": "application/json"}
    url = f"{TELEGRAM_API_URL}/bot{parse.quote(bot_token)}/sendMessage"
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        log(f"Telegram notification failed. HTTP {exc.code}: {' '.join(body.split())[:400]}")
        return False
    except error.URLError as exc:
        log(f"Telegram notification failed: {exc}")
        return False

    result = json.loads(body)
    if not result.get("ok"):
        log(f"Telegram notification failed: {result}")
        return False
    return True


def choose_from_templates(client: RunpodClient) -> TemplateChoice:
    templates = client.list_templates()
    if not templates:
        raise RuntimeError("No Pod templates found on this Runpod account.")
    print("Available templates:")
    for index, template in enumerate(templates, start=1):
        ports = ", ".join(template.ports) if template.ports else "-"
        print(f"  {index}. {template.name} | {template.template_id} | {template.image_name} | ports: {ports}")
    while True:
        raw_value = input("Select template number: ").strip()
        try:
            selected = int(raw_value)
        except ValueError:
            print("Enter a valid number.")
            continue
        if 1 <= selected <= len(templates):
            return templates[selected - 1]
        print("Option out of range.")


def choose_from_volumes(client: RunpodClient) -> NetworkVolumeChoice:
    volumes = client.list_network_volumes()
    if not volumes:
        raise RuntimeError("No network volumes found on this Runpod account.")
    print("Available network volumes:")
    for index, volume in enumerate(volumes, start=1):
        print(f"  {index}. {volume.name} | {volume.volume_id} | {volume.data_center_id} | {volume.size} GB")
    while True:
        raw_value = input("Select network volume number: ").strip()
        try:
            selected = int(raw_value)
        except ValueError:
            print("Enter a valid number.")
            continue
        if 1 <= selected <= len(volumes):
            return volumes[selected - 1]
        print("Option out of range.")


def configure(client: Optional[RunpodClient] = None) -> Dict[str, Any]:
    config = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
    api_key = prompt_text("Runpod API key", config.get("api_key") or None, secret=True)
    config["api_key"] = api_key
    client = RunpodClient(api_key)

    cloud_option = prompt_choice(
        "Choose cloud type",
        [
            {"label": "Community", "value": "COMMUNITY"},
            {"label": "Secure", "value": "SECURE"},
        ],
        default_index=1 if config.get("cloud_type", "COMMUNITY") == "COMMUNITY" else 2,
    )
    config["cloud_type"] = cloud_option["value"]
    config["poll_interval_seconds"] = prompt_int(
        "Poll interval in seconds",
        int(config.get("poll_interval_seconds", 120)),
        minimum=15,
    )
    config["volume_mount_path"] = prompt_text(
        "Volume mount path",
        config.get("volume_mount_path", "/workspace"),
    )
    config["pod_name_prefix"] = prompt_text(
        "Pod name prefix",
        config.get("pod_name_prefix", "comfyui-auto"),
    )

    data_center_raw = prompt_optional(
        "Preferred data center IDs, comma-separated (leave empty for any)",
        ",".join(config.get("data_center_ids", [])),
    )
    config["data_center_ids"] = [part.strip() for part in data_center_raw.split(",") if part.strip()]

    print("")
    template = choose_from_templates(client)
    config["template_id"] = template.template_id
    config["template_name"] = template.name

    print("")
    volume = choose_from_volumes(client)
    config["network_volume_id"] = volume.volume_id
    config["network_volume_name"] = volume.name
    if volume.data_center_id and not config["data_center_ids"]:
        config["data_center_ids"] = [volume.data_center_id]

    print("")
    print("Telegram notifications are optional. Leave both fields empty to disable them.")
    config["telegram_bot_token"] = prompt_optional_secret(
        "Telegram bot token",
        config.get("telegram_bot_token", ""),
    )
    config["telegram_chat_id"] = prompt_optional(
        "Telegram chat ID",
        config.get("telegram_chat_id", ""),
    )

    save_json(CONFIG_PATH, config)
    log(f"Saved configuration to {CONFIG_PATH}")
    return config


def ensure_config() -> Dict[str, Any]:
    config = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
    required_keys = ["api_key", "template_id", "network_volume_id"]
    if any(not config.get(key) for key in required_keys):
        log("Configuration is incomplete. Starting setup.")
        return configure()
    return config


def choose_gpu_request() -> Dict[str, Any]:
    return prompt_choice("Choose the GPU target:", GPU_OPTIONS, default_index=6)


def summarize_request(gpu_option: Dict[str, Any], container_disk_gb: int, config: Dict[str, Any]) -> None:
    log("Watcher settings:")
    log(f"  GPU choice: {gpu_option['label']}")
    for gpu_type_id in gpu_option["gpu_type_ids"]:
        log(f"  GPU type id: {gpu_type_id}")
    log(f"  Container disk: {container_disk_gb} GB")
    log(f"  Template: {config.get('template_name', '')} ({config['template_id']})")
    log(f"  Network volume: {config.get('network_volume_name', '')} ({config['network_volume_id']})")
    log(f"  Cloud type: {config['cloud_type']}")
    log(f"  Poll interval: {config['poll_interval_seconds']} seconds")
    if config.get("data_center_ids"):
        log(f"  Preferred datacenters: {', '.join(config['data_center_ids'])}")


def find_existing_active_pod(client: RunpodClient, pod_name_prefix: str, network_volume_id: str) -> Optional[Dict[str, Any]]:
    for pod in client.list_pods():
        name = pod.get("name", "")
        desired_status = pod.get("desiredStatus", "")
        pod_network_volume = (pod.get("networkVolume") or {}).get("id")
        if name.startswith(pod_name_prefix) and desired_status != "EXITED" and pod_network_volume == network_volume_id:
            return pod
    return None


def build_pod_payload(config: Dict[str, Any], gpu_option: Dict[str, Any], container_disk_gb: int) -> Dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload: Dict[str, Any] = {
        "name": f"{config['pod_name_prefix']}-{gpu_option['key']}-{timestamp}",
        "cloudType": config["cloud_type"],
        "computeType": "GPU",
        "containerDiskInGb": container_disk_gb,
        "desiredStatus": "RUNNING",
        "gpuCount": 1,
        "gpuTypeIds": gpu_option["gpu_type_ids"],
        "gpuTypePriority": "custom",
        "interruptible": False,
        "networkVolumeId": config["network_volume_id"],
        "templateId": config["template_id"],
        "volumeMountPath": config["volume_mount_path"],
    }
    data_center_ids = config.get("data_center_ids") or []
    if data_center_ids:
        payload["dataCenterIds"] = data_center_ids
        payload["dataCenterPriority"] = "custom"
    return payload


def is_gpu_available(result: Dict[str, Any]) -> bool:
    lowest_price = result.get("lowestPrice")
    if not lowest_price:
        return False
    stock_status = lowest_price.get("stockStatus", "None")
    counts = lowest_price.get("availableGpuCounts") or []
    if 1 in counts:
        return True
    return stock_status not in {"None", "Unavailable", "OutOfStock", "NoStock"}


def write_state(payload: Dict[str, Any]) -> None:
    save_json(STATE_PATH, payload)


def should_retry_create_error(exc: RunpodHttpError) -> bool:
    if exc.api_name != "REST":
        return False
    if exc.status_code in {400, 404, 409, 429, 500, 502, 503, 504}:
        return True
    return False


def create_and_record_pod(
    client: RunpodClient,
    config: Dict[str, Any],
    gpu_option: Dict[str, Any],
    container_disk_gb: int,
    selected_gpu_type_id: Optional[str] = None,
) -> bool:
    create_option = {
        "key": gpu_option["key"],
        "label": gpu_option["label"],
        "gpu_type_ids": [selected_gpu_type_id] if selected_gpu_type_id else gpu_option["gpu_type_ids"],
    }
    payload = build_pod_payload(config, create_option, container_disk_gb)
    try:
        pod = client.create_pod(payload)
    except RunpodHttpError as exc:
        if should_retry_create_error(exc):
            compact_body = " ".join(exc.body.split())
            log(f"Create attempt failed; will retry later. REST HTTP {exc.status_code}: {compact_body[:400]}")
            return False
        raise

    pod_id = pod.get("id", "")
    pod_name = pod.get("name", "(unnamed)")
    log(f"Pod created: {pod_name} | id={pod_id}")
    write_state(
        {
            "last_created_at": datetime.now().isoformat(),
            "container_disk_gb": container_disk_gb,
            "gpu_choice_key": gpu_option["key"],
            "selected_gpu_type_id": selected_gpu_type_id or ",".join(gpu_option["gpu_type_ids"]),
            "pod_id": pod_id,
            "pod_name": pod_name,
        }
    )
    telegram_text = "\n".join(
        [
            "Runpod Pod started",
            f"Name: {pod_name}",
            f"ID: {pod_id or '-'}",
            f"GPU: {selected_gpu_type_id or gpu_option['label']}",
            f"Template: {config.get('template_name', '-')}",
            f"Volume: {config.get('network_volume_name', '-')}",
            f"Container disk: {container_disk_gb} GB",
        ]
    )
    if send_telegram_message(config, telegram_text):
        log("Telegram notification sent.")
    return True


def get_client_for_readonly_command() -> RunpodClient:
    config = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
    api_key = config.get("api_key") or prompt_text("Runpod API key", secret=True)
    return RunpodClient(api_key)


def list_account_resources() -> int:
    client = get_client_for_readonly_command()

    print("Templates:")
    templates = client.list_templates()
    if not templates:
        print("  No templates found.")
    for template in templates:
        ports = ", ".join(template.ports) if template.ports else "-"
        print(f"  {template.name}")
        print(f"    id: {template.template_id}")
        print(f"    image: {template.image_name or '-'}")
        print(f"    ports: {ports}")

    print("")
    print("Network volumes:")
    volumes = client.list_network_volumes()
    if not volumes:
        print("  No network volumes found.")
    for volume in volumes:
        print(f"  {volume.name}")
        print(f"    id: {volume.volume_id}")
        print(f"    data_center: {volume.data_center_id or '-'}")
        print(f"    size: {volume.size if volume.size is not None else '-'} GB")

    return 0


def test_telegram() -> int:
    config = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
    if not config.get("telegram_bot_token") or not config.get("telegram_chat_id"):
        print("Telegram is not configured. Run:")
        print(r"  D:\Scripts\runpod_auto_pod_watcher\run_watcher.bat telegram-config")
        return 1
    text = "Runpod Auto Pod Watcher test message"
    if send_telegram_message(config, text):
        print("Telegram test message sent.")
        return 0
    print("Telegram test message failed. Check watcher.log.")
    return 1


def configure_telegram() -> int:
    config = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
    print("Telegram configuration")
    print("Bot token must be the full token, for example 123456:ABC...")
    config["telegram_bot_token"] = prompt_optional_secret(
        "Telegram bot token",
        config.get("telegram_bot_token", ""),
    )
    config["telegram_chat_id"] = prompt_text(
        "Telegram chat ID",
        config.get("telegram_chat_id") or None,
    )
    save_json(CONFIG_PATH, config)
    log("Saved Telegram configuration.")
    if send_telegram_message(config, "Runpod Auto Pod Watcher Telegram is configured."):
        print("Telegram test message sent.")
        return 0
    print("Telegram config saved, but test message failed. Check watcher.log.")
    return 1


def run_watcher() -> int:
    config = ensure_config()
    client = RunpodClient(config["api_key"])

    gpu_option = choose_gpu_request()
    default_container = 80
    last_state = load_json(STATE_PATH, {})
    if last_state.get("container_disk_gb"):
        default_container = int(last_state["container_disk_gb"])
    container_disk_gb = prompt_int("Temporary container disk size (GB)", default_container, minimum=1)
    summarize_request(gpu_option, container_disk_gb, config)

    existing_pod = find_existing_active_pod(
        client,
        config["pod_name_prefix"],
        config["network_volume_id"],
    )
    if existing_pod:
        log(
            "Active Pod with the same prefix and network volume already exists: "
            f"{existing_pod.get('name')} ({existing_pod.get('id')})"
        )
        answer = input("Continue anyway and allow another Pod to be created? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            log("Aborted by user.")
            return 1

    secure_cloud = config["cloud_type"] == "SECURE"
    interval = int(config["poll_interval_seconds"])
    attempt = 0
    use_direct_create_fallback = False

    while True:
        attempt += 1
        log(f"Polling Runpod for GPU availability. Attempt {attempt}.")

        if use_direct_create_fallback:
            log("GraphQL availability check is unavailable. Trying direct REST Pod creation.")
            if create_and_record_pod(client, config, gpu_option, container_disk_gb):
                return 0
            log(f"Requested GPU is unavailable or Pod cannot be created yet. Sleeping for {interval} seconds.")
            time.sleep(interval)
            continue

        available_result: Optional[Dict[str, Any]] = None

        for gpu_type_id in gpu_option["gpu_type_ids"]:
            try:
                result = client.check_gpu_availability(gpu_type_id, secure_cloud=secure_cloud)
            except RunpodHttpError as exc:
                if exc.api_name == "GraphQL" and exc.status_code == 403:
                    log("GraphQL availability check was blocked. Switching to direct REST create retry mode.")
                    use_direct_create_fallback = True
                    break
                raise
            lowest_price = result.get("lowestPrice") or {}
            stock_status = lowest_price.get("stockStatus", "None")
            available_counts = lowest_price.get("availableGpuCounts") or []
            price = lowest_price.get("uninterruptablePrice")
            log(
                f"  {result.get('displayName', gpu_type_id)} | "
                f"stock={stock_status} | counts={available_counts} | price={price}"
            )
            if is_gpu_available(result):
                available_result = result
                break

        if use_direct_create_fallback:
            continue

        if available_result:
            selected_gpu_type = available_result.get("id") or available_result.get("displayName")
            log(f"GPU is available. Creating Pod with {selected_gpu_type}.")
            if create_and_record_pod(client, config, gpu_option, container_disk_gb, selected_gpu_type):
                return 0

        log(f"Requested GPU is unavailable. Sleeping for {interval} seconds.")
        time.sleep(interval)


def main(argv: List[str]) -> int:
    if len(argv) > 1 and argv[1] in {"--configure", "configure"}:
        configure()
        return 0
    if len(argv) > 1 and argv[1] in {"--list", "list", "ids"}:
        return list_account_resources()
    if len(argv) > 1 and argv[1] in {"--telegram-test", "telegram-test", "test-telegram"}:
        return test_telegram()
    if len(argv) > 1 and argv[1] in {"--telegram-config", "telegram-config", "configure-telegram"}:
        return configure_telegram()
    if len(argv) > 1 and argv[1] in {"--help", "-h", "help"}:
        print("Runpod Auto Pod Watcher")
        print("")
        print("Commands:")
        print("  python runpod_auto_pod_watcher.py           Start the watcher")
        print("  python runpod_auto_pod_watcher.py configure Re-run configuration")
        print("  python runpod_auto_pod_watcher.py list      Print template and volume IDs")
        print("  python runpod_auto_pod_watcher.py telegram-config")
        print("  python runpod_auto_pod_watcher.py telegram-test")
        return 0
    try:
        return run_watcher()
    except KeyboardInterrupt:
        log("Stopped by user.")
        return 130
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

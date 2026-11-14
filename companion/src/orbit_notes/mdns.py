"""Advertise `_orbitnotes._tcp` on the LAN. Instance name == configured name (NsdManager matches on it)."""

from __future__ import annotations

import ipaddress
import socket

import ifaddr
from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

SERVICE_TYPE = "_orbitnotes._tcp.local."
SKIP_IFACES = ("docker", "br-", "veth", "virbr", "lo", "tailscale", "zt")


def lan_ipv4() -> list[str]:
    """Private IPv4 addresses of real network interfaces (no loopback, link-local or container bridges)."""
    ips = []
    for adapter in ifaddr.get_adapters():
        if adapter.nice_name.startswith(SKIP_IFACES):
            continue
        for ip in adapter.ips:
            if isinstance(ip.ip, str):
                addr = ipaddress.IPv4Address(ip.ip)
                if addr.is_private and not (addr.is_loopback or addr.is_link_local):
                    ips.append(ip.ip)
    return ips


def service_info(name: str, port: int, ips: list[str]) -> ServiceInfo:
    host = socket.gethostname().split(".")[0]
    return ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        port=port,
        properties={"name": name, "api": "1"},
        addresses=[socket.inet_aton(ip) for ip in ips],
        server=f"{host}.local.",
    )


class Advertiser:
    def __init__(self, name: str, port: int):
        self.ips = lan_ipv4()
        self.info = service_info(name, port, self.ips)
        self.zc: AsyncZeroconf | None = None

    async def start(self) -> None:
        if not self.ips:
            print("mDNS: no LAN IPv4 address found, not advertising")
            return
        self.zc = AsyncZeroconf(ip_version=IPVersion.V4Only, interfaces=self.ips)
        await self.zc.async_register_service(self.info)
        print(f"mDNS: advertising {self.info.name} on {', '.join(self.ips)}:{self.info.port}")

    async def stop(self) -> None:
        if self.zc:
            await self.zc.async_unregister_service(self.info)
            await self.zc.async_close()

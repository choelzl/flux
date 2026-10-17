"""The sandbox settings an admin gives a loop: raw network, Nix packages.

Run through web_ui.py (D1171: the steps of the area `sandbox`, tests/areas.py)."""

from __future__ import annotations

import json


def flows(r):
    b = r.b

    def raw_network_setting():
        """Only an admin can opt an individual app into raw transports, saved from its checkbox."""
        r.login("ada")
        r.page("#/u/bob/app/sw/settings/loop", "document.querySelector('#adv-raw-network')", "the app's raw-network setting")
        r.check("Raw TCP/UDP is off by default", not b.js("return document.querySelector('#adv-raw-network').checked"))
        b.click("#adv-raw-network")
        b.wait("[...document.querySelectorAll('#main .save-mark')].some(x => x.dataset.state === 'saved')", what="raw networking saved")
        got = json.loads(r.api("/apps/sw/env?owner=bob")["body"])
        r.check("The admin checkbox enables raw networking for this app", got["advanced"].get("raw_network") is True, str(got["advanced"]))
        r.page("#/u/bob/app/sw/settings/loop", "document.querySelector('#adv-raw-network')", "the saved app setting")
        r.check("Raw TCP/UDP remains enabled after reopening Settings", b.js("return document.querySelector('#adv-raw-network').checked"))
        r.clean("raw-network admin setting")
        r.login("bob")
        r.page("#/app/sw/settings/loop", "document.querySelector('#main').textContent.includes('(admin only)')", "the owner's read-only advanced settings")
        r.check("The owner sees raw networking enabled without an editable checkbox", not b.js("return !!document.querySelector('#adv-raw-network')")
                and "raw TCP/UDP allowed" in r.text())
        r.check("The owner cannot change raw networking through the API", r.api("/apps/sw/advanced", "PUT", {"raw_network": False})["status"] == 403)
        b.js("window.__e2e.errors.splice(0); return 1")           # the expected refusal
        r.login("ada")
        r.page("#/u/bob/app/sw/settings/loop", "document.querySelector('#adv-raw-network')", "disabling raw networking")
        b.click("#adv-raw-network")
        b.wait("[...document.querySelectorAll('#main .save-mark')].some(x => x.dataset.state === 'saved')", what="proxy-only networking saved")
        got = json.loads(r.api("/apps/sw/env?owner=bob")["body"])
        r.check("Disabling raw TCP/UDP restores the proxy-only default", "raw_network" not in got["advanced"], str(got["advanced"]))
        r.clean("raw-network setting disabled")
        r.login("bob")
    r.step("raw network setting", raw_network_setting, setup=True)

    def nix_packages_setting():
        """Extra tools are selected per loop by an admin and preserved by other setting changes."""
        r.login("ada")
        r.page("#/u/bob/app/sw/settings/loop", "document.querySelector('#adv-nix-packages')", "the app's Nix packages")
        b.type("#adv-nix-packages", "jq\nripgrep\njq")
        b.type("#adv-nixchip-packages", "verilator")
        b.js("document.querySelector('#adv-nixchip-packages').dispatchEvent(new Event('change', {bubbles: true})); return 1")
        b.wait("[...document.querySelectorAll('#main .save-mark')].some(x => x.dataset.state === 'saved')", what="Nix packages saved")
        got = json.loads(r.api("/apps/sw/env?owner=bob")["body"])["advanced"]
        r.check("Nix packages save under their own sources, without duplicates", got.get("nix_packages") == ["jq", "ripgrep"]
                and got.get("nixchip_packages") == ["verilator"], str(got))
        r.page("#/u/bob/app/sw/settings/loop", "document.querySelector('#adv-nix-packages')", "the saved packages")
        r.check("Package settings remain after reopening", b.js("return document.querySelector('#adv-nix-packages').value") == "jq\nripgrep"
                and b.js("return document.querySelector('#adv-nixchip-packages').value") == "verilator")
        b.click("#adv-parallel")
        b.wait("[...document.querySelectorAll('#main .save-mark')].some(x => x.dataset.state === 'saved')", what="other advanced settings saved")
        got = json.loads(r.api("/apps/sw/env?owner=bob")["body"])["advanced"]
        r.check("Changing another advanced setting preserves packages", got.get("nix_packages") == ["jq", "ripgrep"]
                and got.get("nixchip_packages") == ["verilator"], str(got))
        r.clean("Nix package admin settings")
        r.login("bob")
        r.page("#/app/sw/settings/loop", "document.querySelector('#main').textContent.includes('(admin only)')", "the owner's package settings")
        r.check("The owner sees both sources without editable fields", not b.js("return !!document.querySelector('#adv-nix-packages')")
                and "Nixpkgs packages: jq, ripgrep" in r.text() and "Nixchip packages: verilator" in r.text())
        r.check("The owner cannot change packages through the API", r.api("/apps/sw/advanced", "PUT", {"nix_packages": ["hello"]})["status"] == 403)
        b.js("window.__e2e.errors.splice(0); return 1")
        r.clean("Nix package owner settings")
        r.login("ada")
        r.api("/apps/sw/advanced?owner=bob", "PUT", {})
        r.login("bob")
    r.step("nix packages setting", nix_packages_setting, setup=True)

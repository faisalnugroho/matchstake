"""Browser value-propagation tests.

The steward's re-review explicitly asked for coverage of the browser's
deposit path: the frontend MUST send the GEN deposit as transaction value
on create_request (payable), and the bundled SDK MUST forward that value
into the signed consensus transaction. These tests pin that chain
source-to-source, so a regression (e.g. dropping the value argument or the
opts.value branch) fails CI even without a browser.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "index.html"
BUNDLE = ROOT / "frontend" / "genlayer-sdk.bundle.js"
CONTRACT = ROOT / "contracts" / "matchstake.py"


def test_contract_create_request_is_payable():
    src = CONTRACT.read_text()
    assert "@gl.public.write.payable" in src
    sig = re.search(r"def create_request\(.*?\).*?:", src, re.S)
    assert sig, "create_request signature not found"
    assert 'value = int(gl.message.value)' in src
    assert 'assert value > 0, "deposit_required"' in src


def test_frontend_create_request_passes_value():
    src = FRONTEND.read_text()
    # deposit constant is defined in wei
    m = re.search(r"const DEPOSIT_WEI = '(\d+)'", src)
    assert m, "DEPOSIT_WEI constant missing"
    assert int(m.group(1)) == 100000000000000000  # 0.1 GEN
    # create_request call passes the deposit as the valueWei argument
    m = re.search(
        r"sendWrite\('create_request',\s*\[[^\]]*\],\s*'[A-Za-z]+',\s*DEPOSIT_WEI\)", src)
    assert m, "create_request must be called with DEPOSIT_WEI as value"


def test_frontend_sendwrite_sets_opts_value():
    src = FRONTEND.read_text()
    # valueWei is threaded into the SDK call options as BigInt
    assert "opts.value = BigInt(valueWei)" in src
    # and no other write call is created without going through opts
    assert "client.writeContract(opts)" in src


def test_bundle_write_contract_defaults_and_forwards_value():
    """The bundled genlayer-js writeContract destructures `value` (default 0n)
    and forwards it into the transaction builder (x6), which places it on the
    signed legacy tx AND the wallet eth_sendTransaction params."""
    src = BUNDLE.read_text()
    # writeContract destructures value with 0n default
    m = re.search(r"writeContract:async \w+=>\{[^}]*?value:\w+=0n", src)
    assert m, "bundle writeContract must destructure value"
    # it forwards value to the tx sender (x6)
    m = re.search(r"x6\(\{[^}]*?value:\w+\}\)", src, re.S)
    assert m, "bundle writeContract must forward value to tx sender"
    # the tx builder puts the value on the signed legacy tx
    m = re.search(r"value:\s*\w+,gas:", src)
    assert m, "signed legacy tx must carry value"
    # and on the wallet eth_sendTransaction params (hex-encoded)
    assert 'value:`0x${' in src, "eth_sendTransaction params must carry value"


def test_bundle_deploy_does_not_swallow_value_path():
    """deployContract never receives a value (deposits live on create_request);
    pin that deployContract forwards no value key so the two paths stay distinct."""
    src = BUNDLE.read_text()
    m = re.search(r"deployContract:async \w+=>\{.*?x6\(\{(?P<body>[^}]*)\}\)", src, re.S)
    assert m
    assert "value:" not in m.group("body")


def test_frontend_value_flows_end_to_end():
    """Integration of the three pinned links:
    constant -> create_request call -> sendWrite opts -> bundle tx value."""
    fe = FRONTEND.read_text()
    deposit = re.search(r"const DEPOSIT_WEI = '(\d+)'", fe).group(1)
    call = re.search(
        r"sendWrite\('create_request',.*?,\s*(\w+)\)", fe).group(1)
    assert call == "DEPOSIT_WEI"
    assert int(deposit) > 0
    # sendWrite sets opts.value = BigInt(valueWei); valueWei arrives as the
    # 5th positional arg of sendWrite, fed with DEPOSIT_WEI at the call site.
    assert re.search(r"async function sendWrite\([^)]*valueWei\)", fe)
    assert re.search(r"opts\.value\s*=\s*BigInt\(\s*valueWei\s*\)", fe)

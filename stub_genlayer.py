"""
Minimal stub of the GenLayer python SDK, used only to unit-test modappeal.py
logic offline. This is NOT the real genlayer runtime -- it exists so the
contract's business logic (state machine, majority rule, economics) can be
exercised with plain python before ever touching GenLayer Studio.
"""
import sys
import types
import hashlib
import datetime as _dt


class Address(str):
    def __new__(cls, value):
        return str.__new__(cls, str(value).lower())


def u256(v=0):
    return int(v)


class TreeMap(dict):
    def __class_getitem__(cls, item):
        return dict


class DynArray(list):
    def __class_getitem__(cls, item):
        return list


def allow_storage(cls):
    return cls


class _Storage:
    @staticmethod
    def inmem_allocate(type_, *args, **kwargs):
        return type_(*args, **kwargs)


class _Message:
    def __init__(self):
        self.sender_address = Address("0xsender")
        self.value = u256(0)


class Return:
    def __init__(self, calldata):
        self.calldata = calldata


class _VM:
    @staticmethod
    def run_nondet_unsafe(leader_fn, validator_fn):
        result = leader_fn()
        wrapped = Return(result)
        if not validator_fn(wrapped):
            raise Exception("validator rejected leader result")
        return result

    Return = Return


class _Web:
    @staticmethod
    def render(url):
        return f"[stub content for {url}]"


class _Nondet:
    # Overridden per-test via monkeypatch of gl.nondet.exec_prompt
    @staticmethod
    def exec_prompt(prompt):
        return "NO_VIOLATION"

    web = _Web()


class _ContractHandle:
    def __init__(self, address):
        self.address = address

    def emit_transfer(self, value=0, on=None):
        gl.emitted.append((self.address, int(value), on))


def get_contract_at(address):
    return _ContractHandle(address)


class _PublicWrite:
    def __call__(self, fn):
        return fn

    class payable:
        def __new__(cls, fn):
            return fn


class _Public:
    write = _PublicWrite()
    view = staticmethod(lambda fn: fn)


class Contract:
    """Mimics real GenVM behavior: class-level annotated TreeMap/DynArray
    fields are auto-initialized to empty containers before __init__ runs,
    without the contract needing to assign them itself."""
    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        for klass in reversed(cls.__mro__):
            for name, type_ in vars(klass).get("__annotations__", {}).items():
                if type_ is dict:
                    object.__setattr__(obj, name, TreeMap())
                elif type_ is list:
                    object.__setattr__(obj, name, DynArray())
        return obj


gl = types.SimpleNamespace(
    Contract=Contract,
    public=_Public(),
    message=_Message(),
    vm=_VM(),
    nondet=_Nondet(),
    storage=_Storage(),
    get_contract_at=get_contract_at,
    emitted=[],
)

# Make `from genlayer import *` resolve these names inside modappeal.py
genlayer_module = types.ModuleType("genlayer")
genlayer_module.gl = gl
genlayer_module.Address = Address
genlayer_module.u256 = u256
genlayer_module.TreeMap = TreeMap
genlayer_module.DynArray = DynArray
genlayer_module.allow_storage = allow_storage
genlayer_module.__all__ = ["gl", "Address", "u256", "TreeMap", "DynArray", "allow_storage"]
sys.modules["genlayer"] = genlayer_module


def commitment_hash(verdict: str, salt: str) -> str:
    return hashlib.sha256((verdict + salt).encode()).hexdigest()


class assert_raises:
    """Tiny pytest.raises replacement so tests run without pytest installed."""
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            raise AssertionError("expected an exception but none was raised")
        return True


_FAKE_CLOCK = {"value": _dt.datetime(2024, 1, 1)}


def install_fake_clock(modappeal_module):
    """Patch ModAppeal._now so tests can control elapsed time without
    real sleeps, since the production code reads datetime.datetime.now()."""
    modappeal_module.ModAppeal._now = lambda self: _FAKE_CLOCK["value"]


def set_time(seconds_from_epoch_start):
    _FAKE_CLOCK["value"] = _dt.datetime(2024, 1, 1) + _dt.timedelta(seconds=seconds_from_epoch_start)


def reset():
    gl.message.sender_address = Address("0xsender")
    gl.message.value = u256(0)
    gl.emitted.clear()
    set_time(0)

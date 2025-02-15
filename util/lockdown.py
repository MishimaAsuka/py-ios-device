import os
import plistlib
import sys
import uuid
import platform
from util import logging
from distutils.version import LooseVersion
from pathlib import Path
from typing import Optional, Dict, Any, Mapping

from .exceptions import PairingError, NotTrustedError, FatalPairingError, NotPairedError, CannotStopSessionError
from .exceptions import StartServiceError, InitializationError
from .plist_service import PlistService
from .ssl import make_certs_and_key
from .usbmux import MuxDevice, UsbmuxdClient
from .utils import DictAttrProperty, cached_property

__all__ = ['LockdownClient']
log = logging.getLogger(__name__)


class LockdownClient:
    label = 'pyMobileDevice'
    udid = DictAttrProperty('device_info', 'UniqueDeviceID')
    unique_chip_id = DictAttrProperty('device_info', 'UniqueChipID')
    ios_version = DictAttrProperty('device_info', 'ProductVersion', LooseVersion)

    def __init__(
            self,
            udid: Optional[str] = None,
            device: Optional[MuxDevice] = None,
            cache_dir: str = '.cache/pymobiledevice',
    ):
        self.cache_dir = cache_dir
        self.record = None  # type: Optional[Dict[str, Any]]
        self.sslfile = None
        self.session_id = None
        self.host_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, platform.node())).upper()
        self.svc = PlistService(62078, udid, device)
        self._verify_query_type()
        self.device_info = self.get_value()
        self.paired = self._pair()

    def _verify_query_type(self):
        query_type = self.svc.plist_request({'Request': 'QueryType'}).get('Type')
        if query_type != 'com.apple.mobile.lockdown':
            raise InitializationError(f'Unexpected {query_type}')

    @cached_property
    def identifier(self):
        if self.udid:
            return self.udid
        elif self.unique_chip_id:
            return f'{self.unique_chip_id:x}'
        raise InitializationError('Unable to determine UDID or ECID - failing')

    def _pair(self):
        if self._validate_pairing():
            return True
        self._pair_full()
        self.svc = PlistService(62078, self.udid, self.svc.device)
        if self._validate_pairing():
            return True
        raise FatalPairingError

    def _get_pair_record(self) -> Optional[Dict[str, Any]]:
        lockdown_path = _get_lockdown_dir()
        itunes_lockdown_path = lockdown_path.joinpath(f'{self.identifier}.plist')
        try:  # 如果没有 lockdown 权限，则使用自有缓存证书，建议开启 lockdown 权限，避免重复认证
            if itunes_lockdown_path.exists():
                log.debug(f'Using iTunes pair record: {itunes_lockdown_path}')
                with itunes_lockdown_path.open('rb') as f:
                    return plistlib.load(f)
        except Exception as E:
            log.error(f'{E}')
            log.debug(f'No iTunes pairing record found for device {self.identifier}')
            if self.ios_version > LooseVersion('13.0'):
                log.debug('Getting pair record from usbmuxd')
                return UsbmuxdClient().get_pair_record(self.udid)
            elif read_home_file(self.cache_dir, f'{self.identifier}.plist'):
                log.debug(f'Found pymobiledevice pairing record for device {self.udid}')
                return plistlib.loads(read_home_file(self.cache_dir, f'{self.identifier}.plist'))

            log.debug(f'No pymobiledevice pairing record found for device {self.identifier}')
            return None

    def _validate_pairing(self):
        pair_record = self._get_pair_record()
        if not pair_record:
            return False
        self.record = pair_record
        if self.ios_version < LooseVersion('11.0'):  # 11 以下需要双向认证
            resp = self._plist_request('ValidatePair', PairRecord=pair_record)
            if not resp or 'Error' in resp:
                log.error(f'Failed to ValidatePair: {resp}')
                return False

        self.host_id = pair_record.get('HostID', self.host_id)
        system_buid = pair_record.get('SystemBUID') or str(uuid.uuid3(uuid.NAMESPACE_DNS, platform.node())).upper()
        resp = self._plist_request('StartSession', HostID=self.host_id, SystemBUID=system_buid)
        self.session_id = resp.get('SessionID')
        if resp.get('EnableSessionSSL'):
            self.sslfile = write_home_file(
                self.cache_dir,
                f'{self.identifier}_ssl.txt',
                pair_record['HostCertificate'] + b'\n' + pair_record['HostPrivateKey']
            )
            self.svc.ssl_start(self.sslfile, self.sslfile)

        return True

    def _pair_full(self):
        device_public_key = self.get_value('', 'DevicePublicKey')
        if not device_public_key:
            log.error('Unable to retrieve DevicePublicKey')
            return False

        log.debug('Creating host key & certificate')
        cert_pem, priv_key_pem, dev_cert_pem = make_certs_and_key(device_public_key)
        pair_record = {
            'DevicePublicKey': plistlib.Data(device_public_key),
            'DeviceCertificate': plistlib.Data(dev_cert_pem),
            'HostCertificate': plistlib.Data(cert_pem),
            'HostID': self.host_id,
            'RootCertificate': plistlib.Data(cert_pem),
            'SystemBUID': '30142955-444094379208051516'
        }

        pair = self.svc.plist_request({'Label': self.label, 'Request': 'Pair', 'PairRecord': pair_record})
        if pair and pair.get('Result') == 'Success' or 'EscrowBag' in pair:
            pair_record['HostPrivateKey'] = plistlib.Data(priv_key_pem)
            pair_record['EscrowBag'] = pair.get('EscrowBag')
            write_home_file(self.cache_dir, '%s.plist' % self.identifier, plistlib.dumps(pair_record))
            return True
        elif pair and pair.get('Error') == 'PasswordProtected':
            self.svc.close()
            raise NotTrustedError
        else:
            log.error(pair.get('Error'))
            self.svc.close()
            raise PairingError

    def _plist_request(self, request: str, fields: Optional[Mapping[str, Any]] = None, label=True, **kwargs):
        req = {'Request': request, 'Label': self.label} if label else {'Request': request}
        if fields:
            req.update(fields)
        for k, v in kwargs.items():
            if v:
                req[k] = v
        return self.svc.plist_request(req)

    def get_value(self, domain=None, key=None):
        if isinstance(key, str) and self.record and key in self.record:
            return self.record[key]
        resp = self._plist_request('GetValue', Domain=domain, Key=key)
        if resp:
            value = resp.get('Value')
            if hasattr(value, 'data'):
                return value.data
            return value
        return None

    def set_value(self, value, domain=None, key=None):
        resp = self._plist_request('SetValue', {'Value': value}, Domain=domain, Key=key)
        log.debug(resp)
        return resp

    def start_service(self, name: str, escrow_bag=None) -> PlistService:
        if not self.paired:
            raise NotPairedError(f'Unable to start service={name!r} - not paired')
        elif not name:
            raise ValueError('Name must be a valid string')

        escrow_bag = self.record['EscrowBag'] if escrow_bag is True else escrow_bag
        resp = self._plist_request('StartService', Service=name, EscrowBag=escrow_bag)
        if not resp:
            raise StartServiceError(f'Unable to start service={name!r}')
        elif resp.get('Error'):
            if resp.get('Error') == 'PasswordProtected':
                raise StartServiceError(f'Unable to start service={name!r} - a password must be entered on the device')
            error = resp.get('Error')
            raise StartServiceError(f'Unable to start service={name!r} - {error}')

        plist_service = PlistService(
            resp.get('Port'), self.udid, ssl_file=self.sslfile if resp.get('EnableServiceSSL', False) else None
        )
        return plist_service

    def stop_session(self):
        if self.session_id and self.svc:
            resp = self._plist_request('StopSession', SessionID=self.session_id)
            self.session_id = None
            if not resp or resp.get('Result') != 'Success':
                raise CannotStopSessionError(resp)
            return resp

    def enter_recovery(self):
        log.debug(self.svc.plist_request({'Request': 'EnterRecovery'}))


def get_home_path(foldername: str, filename: str) -> Path:
    path = Path('~').expanduser().joinpath(foldername)
    if not path.exists():
        path.mkdir(parents=True)
    return path.joinpath(filename)


def read_home_file(foldername: str, filename: str) -> Optional[bytes]:
    path = get_home_path(foldername, filename)
    if not path.exists():
        return None
    with path.open('rb') as f:
        return f.read()


def write_home_file(foldername: str, filename: str, data: bytes) -> str:
    path = get_home_path(foldername, filename)
    with path.open('wb') as f:
        f.write(data)
    return path.as_posix()


def _get_lockdown_dir():
    if sys.platform == 'win32':
        return Path(os.environ['ALLUSERSPROFILE'] + '/Apple/Lockdown/')
    elif sys.platform == "darwin":
        return Path('/var/db/lockdown/')
    elif sys.platform == "linux":
        return Path('/var/lib/lockdown/')

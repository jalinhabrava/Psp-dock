"""Live identity classification must not promote a lab disk to real Dock."""
import copy
import unittest
from unittest.mock import Mock

import psp_dock.devices.bridge_hid as bridge_hid
from psp_dock.devices.psp_dock_identity import DockIdentityError, probe_dock


SESSION = '0102030405060708090a0b0c0d0e0f10'
STABLE_PATH = r'\\?\hid#vid_054c&pid_01cb#psp#{hid-guid}'
MSC_PATH = r'\\?\hid#vid_054c&pid_01cc&mi_00#psp#{hid-guid}'


def candidate(product=0x01CB, revision=0x0104):
    return {'path': STABLE_PATH if product == 0x01CB else MSC_PATH,
            'vendor_id': 0x054C, 'product_id': product, 'revision': revision,
            'status': {'session': SESSION, 'version': 1, 'mode': 2,
                       'connected': True, 'pending': False, 'consumed': False,
                       'sequence': 0, 'result': 0}}


def reports(*devices):
    return {product: {'supported': True, 'errors': [],
                      'devices': [copy.deepcopy(d) for d in devices
                                  if d['product_id'] == product]}
            for product in (0x01CB, 0x01CC)}


def classify(data):
    return probe_dock(probe=lambda *, expected_product: data[expected_product])


class DockIdentityTests(unittest.TestCase):
    def test_absence_is_not_an_error_and_both_products_are_checked(self):
        reader = Mock(side_effect=lambda *, expected_product: reports()[expected_product])
        self.assertIsNone(probe_dock(probe=reader))
        self.assertEqual([c.kwargs for c in reader.call_args_list],
                         [{'expected_product': 0x01CB}, {'expected_product': 0x01CC}])

    def test_stable_controller_and_synthetic_disk_have_different_permissions(self):
        stable = classify(reports(candidate()))
        lab = classify(reports(candidate(0x01CC, 0x0207)))
        self.assertEqual(stable, {'path': STABLE_PATH, 'session': SESSION,
                         'product_id': 0x01CB, 'revision': 0x0104,
                         'mode': 'hid_manual', 'can_play': True, 'synthetic_storage': False})
        self.assertEqual(lab, {'path': MSC_PATH, 'session': SESSION,
                         'product_id': 0x01CC, 'revision': 0x0207,
                         'mode': 'msc_diagnostic', 'can_play': False, 'synthetic_storage': True})

    def test_unknown_old_and_crossed_revisions_fail_closed(self):
        for product, revision in ((0x01CB, 0x0204), (0x01CB, 0x0207),
                                  (0x01CC, 0x0104), (0x01CC, 0x0206),
                                  (0x01CC, None), (0x01CC, '0207')):
            with self.subTest(product=product, revision=revision), self.assertRaises(DockIdentityError):
                classify(reports(candidate(product, revision)))

    def test_multiple_candidates_across_or_within_products_are_ambiguous(self):
        for devices in ((candidate(), candidate(0x01CC, 0x0207)),
                        (candidate(), candidate())):
            with self.assertRaises(DockIdentityError):
                classify(reports(*devices))

    def test_incomplete_enumeration_cannot_accept_the_other_controller(self):
        for change in ({'errors': ['feature read failed']}, {'supported': False},
                       {'devices': None}, {'errors': None}):
            data = reports(candidate(0x01CC, 0x0207))
            data[0x01CB].update(change)
            with self.subTest(change=change), self.assertRaises(DockIdentityError):
                classify(data)
        with self.assertRaises(DockIdentityError):
            probe_dock(probe=Mock(side_effect=bridge_hid.BridgeHidError('gone')))

    def test_pending_disconnected_or_malformed_session_never_opens_dock(self):
        changes = ({'connected': False}, {'connected': 1}, {'pending': True},
                   {'consumed': True}, {'sequence': 1}, {'result': 1},
                   {'version': True}, {'mode': 0}, {'session': '00' * 16},
                   {'session': 'zz' * 16}, {'session': SESSION + '00'})
        for change in changes:
            device = candidate()
            device['status'].update(change)
            with self.subTest(change=change), self.assertRaises(DockIdentityError):
                classify(reports(device))

    def test_foreign_path_attributes_and_wrong_msc_interface_are_rejected(self):
        for key, value in (('path', MSC_PATH), ('vendor_id', 0x054D),
                           ('product_id', 0x02D2), ('revision', True)):
            data = reports(candidate())
            data[0x01CB]['devices'][0][key] = value
            with self.subTest(key=key), self.assertRaises(DockIdentityError):
                classify(data)
        data = reports(candidate(0x01CC, 0x0207))
        data[0x01CC]['devices'][0]['path'] = MSC_PATH.replace('mi_00', 'mi_01')
        with self.assertRaises(DockIdentityError):
            classify(data)


if __name__ == '__main__':
    unittest.main()


from .. import PatchExtractorISLES

import os
import numpy as np
import cv2
import time
from scipy.ndimage import interpolation
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import sys


class PatchExtractorISLESForCNN(PatchExtractorISLES):
    """Class for ISLES 2017 patch extraction."""
    def __init__(self,
                 scans_per_batch_train=10, scans_per_batch_valid=9,
                 patches_per_scan_train=50, patches_per_scan_valid=800,
                 test_patches_per_scan=2000,
                 lp_w=25, lp_h=25, lp_d=14,
                 mp_w=15, mp_h=15, mp_d=12,
                 sp_w=7, sp_h=7, sp_d=6,
                 td_th_1=10, td_th_2=256, lpm_d=2, mpm_d=2, spm_d=1,
                 augment_train=True):
        """Initialization of PatchExtractorISLES attributes."""

        self.scans_per_batch_train = scans_per_batch_train
        self.scans_per_batch_valid = scans_per_batch_valid
        self.patches_per_scan_train = patches_per_scan_train
        self.patches_per_scan_valid = patches_per_scan_valid
        self.test_patches_per_scan = test_patches_per_scan
        self.lp_w, self.lp_h, self.lp_d = [lp_w, lp_h, lp_d]
        self.mp_w, self.mp_h, self.mp_d = [mp_w, mp_h, mp_d]
        self.sp_w, self.sp_h, self.sp_d = [sp_w, sp_h, sp_d]

        self.pvs, self.pve = [(self.lp_h - 1) / 2, (self.lp_h + 1) / 2]
        self.phs, self.phe = [(self.lp_w - 1) / 2, (self.lp_w + 1) / 2]

        self.td_th_1 = td_th_1
        self.td_th_2 = td_th_2

        self.lpm_d = lpm_d
        self.mpm_d = mpm_d
        self.spm_d = spm_d

        self.augment_train = augment_train

    def _get_coordinates(self, shape):
        self.h_coord = np.zeros((shape[0], shape[1]))
        self.v_coord = np.zeros((shape[0], shape[1]))
        wh, hh = [shape[0] / 2, shape[1] / 2]
        for r_idx in range(shape[0]):
            for c_idx in range(shape[1]):
                self.h_coord[r_idx, c_idx] = float(c_idx - wh) / wh
                self.v_coord[r_idx, c_idx] = float(r_idx - hh) / hh

    def _allocate_data_memory(self, db, mode):

        if mode == 'train':
            patches_per_scan = self.patches_per_scan_train
            scans_per_batch = self.scans_per_batch_train
        else:
            patches_per_scan = self.patches_per_scan_valid
            scans_per_batch = self.scans_per_batch_valid

        data = {'region_1': {}, 'region_2': {}}
        for i in db.classes:
            data['region_1'][i] = {}
            data['region_1'][i]['l_patch'] =\
                np.zeros((patches_per_scan * scans_per_batch,
                          self.lp_w * self.lp_h * self.lp_d))
            data['region_1'][i]['m_patch'] =\
                np.zeros((patches_per_scan * scans_per_batch,
                          self.mp_w * self.mp_h * self.mp_d))
            data['region_1'][i]['s_patch'] =\
                np.zeros((patches_per_scan * scans_per_batch,
                          self.sp_w * self.sp_h * self.sp_d))
            data['region_2'][i] = {}
            data['region_2'][i]['l_patch'] =\
                np.zeros((patches_per_scan * scans_per_batch,
                          self.lp_w * self.lp_h * self.lp_d))
            data['region_2'][i]['m_patch'] =\
                np.zeros((patches_per_scan * scans_per_batch,
                          self.mp_w * self.mp_h * self.mp_d))
            data['region_2'][i]['s_patch'] =\
                np.zeros((patches_per_scan * scans_per_batch,
                          self.sp_w * self.sp_h * self.sp_d))
        return data

    def _extract_distances_for_point(self, b, shape):

        h, w, d = shape
        dist = np.zeros((self.lp_h, self.lp_w, 2))
        b1 = np.copy(b)
        d1 = [0, self.lp_h, 0, self.lp_w]
        if b1[0] < 0:
            d1[0] = 0 - b1[0]
            b1[0] = 0
        if b1[2] < 0:
            d1[2] = 0 - b1[2]
            b1[2] = 0
        if b1[1] > h:
            d1[1] = self.lp_h - (b1[1] - h)
            b1[1] = h
        if b1[3] > w:
            d1[3] = self.lp_w - (b1[3] - w)
            b1[3] = w

        dist[d1[0]:d1[1], d1[2]:d1[3], 0] = self.h_coord[b1[0]: b1[1], b1[2]: b1[3]]
        dist[d1[0]:d1[1], d1[2]:d1[3], 0] = self.v_coord[b1[0]: b1[1], b1[2]: b1[3]]
        return dist

    def _select_scans_randomly(self, db, mode):

        if mode.startswith('train'):
            scans = db.train_scans
        elif mode == 'valid':
            scans = db.valid_scans

        if mode == 'train':
            scans_per_batch = self.scans_per_batch_train
        else:
            scans_per_batch = self.scans_per_batch_valid

        rs = np.random.choice(len(scans), scans_per_batch, replace=False)
        return [scans[idx] for idx in rs]

    def _verify_border_cases(self, b_, d_, dims):

        if b_[0] < 0:
            d_[0], b_[0] = [0 - b_[0], 0]
        if b_[2] < 0:
            d_[2], b_[2] = [0 - b_[2], 0]
        if b_[1] > dims[0]:
            d_[1], b_[1] = [self.lp_h - (b_[1] - dims[0]), dims[0]]
        if b_[3] > dims[1]:
            d_[3], b_[3] = [self.lp_w - (b_[3] - dims[1]), dims[1]]

        return b_, d_

    def _modality_patches(self, scan, m, volume, b, mode):

        lpm = np.zeros((self.lp_h, self.lp_w, 2))
        mpm = np.zeros((self.mp_h, self.mp_w, 2))
        spm = np.zeros((self.sp_h, self.sp_w, 1))

        b_, d_ = [np.copy(b), [0, self.lp_h, 0, self.lp_w]]
        b_, d_ = self._verify_border_cases(b_, d_, volume.shape)

        h, w, d = volume.shape

        lpm[d_[0]:d_[1], d_[2]:d_[3], 0] =\
            volume[b_[0]: b_[1], b_[2]: b_[3], b_[4]]

        if mode == 'train' and self.augment_train:
            # Shift mirrored patches for augmentation
            b_, d_ = [np.copy(b), [0, self.lp_h, 0, self.lp_w]]
            if h == 256:
                sh_1, sh_2 = [np.random.randint(11) - 5,
                              np.random.randint(7) - 3]
            if h == 192:
                sh_1, sh_2 = [np.random.randint(9) - 4,
                              np.random.randint(5) - 2]
            if h == 128:
                sh_1, sh_2 = [np.random.randint(7) - 3,
                              np.random.randint(3) - 1]

            b_ = [b_[0] + sh_1, b_[1] + sh_1, b_[2] + sh_2, b_[3] + sh_2, b_[4]]
            b_, d_ = self._verify_border_cases(b_, d_, volume.shape)

        lpm[self.lp_h - d_[1]:self.lp_h - d_[0],
            self.lp_w - d_[3]:self.lp_w - d_[2], 1] =\
            volume[h - b_[1]: h - b_[0], b_[2]: b_[3], b_[4]]

        mpm[:, :, 0] = lpm[:, :, 0][(self.lp_h - 1) / 2 - (self.mp_h - 1) / 2:
                                    (self.lp_h - 1) / 2 + (self.mp_h + 1) / 2,
                                    (self.lp_w - 1) / 2 - (self.mp_w - 1) / 2:
                                    (self.lp_w - 1) / 2 + (self.mp_w + 1) / 2]

        mpm[:, :, 1] = lpm[:, :, 1][(self.lp_h - 1) / 2 - (self.mp_h - 1) / 2:
                                    (self.lp_h - 1) / 2 + (self.mp_h + 1) / 2,
                                    (self.lp_w - 1) / 2 - (self.mp_w - 1) / 2:
                                    (self.lp_w - 1) / 2 + (self.mp_w + 1) / 2]

        spm[:, :, 0] = lpm[:, :, 0][(self.lp_h - 1) / 2 - (self.sp_h - 1) / 2:
                                    (self.lp_h - 1) / 2 + (self.sp_h + 1) / 2,
                                    (self.lp_w - 1) / 2 - (self.sp_w - 1) / 2:
                                    (self.lp_w - 1) / 2 + (self.sp_w + 1) / 2]

        return lpm, mpm, spm

    def _class_patches(self, db, scan, volumes, mask, prep, mode):

        if mode == 'train':
            patches_per_scan = self.patches_per_scan_train
        else:
            patches_per_scan = self.patches_per_scan_valid

        lpc =\
            np.zeros((patches_per_scan, self.lp_h * self.lp_w * self.lp_d))
        mpc =\
            np.zeros((patches_per_scan, self.mp_h * self.mp_w * self.mp_d))
        spc =\
            np.zeros((patches_per_scan, self.sp_h * self.sp_w * self.sp_d))

        n_available = len(mask[0])
        if n_available:
            n_select = np.min([patches_per_scan, n_available])
            select = np.random.choice(n_available, n_select, replace=False)
            for s_idx, s in enumerate(select):
                lp = np.zeros((self.lp_h, self.lp_w, self.lp_d))
                mp = np.zeros((self.mp_h, self.mp_w, self.mp_d))
                sp = np.zeros((self.sp_h, self.sp_w, self.sp_d))
                bb = [mask[0][s] - self.pvs, mask[0][s] + self.pve,
                      mask[1][s] - self.phs, mask[1][s] + self.phe,
                      mask[2][s]]
                for i, m in enumerate(db.modalities):
                    lpm, mpm, spm =\
                        self._modality_patches(scan, m, volumes[i], bb, mode)

                    lp[:, :, i * self.lpm_d:(i + 1) * self.lpm_d] = lpm
                    mp[:, :, i * self.mpm_d:(i + 1) * self.mpm_d] = mpm
                    sp[:, :, i * self.spm_d:(i + 1) * self.spm_d] = spm

                lp[:, :, self.lpm_d * db.n_modalities:
                         self.lpm_d * db.n_modalities + 2] =\
                    self._extract_distances_for_point(bb, volumes[0].shape)

                if mode == 'train' and self.augment_train:

                    for sp_idx in range(self.sp_d):
                        jitter1 = 0.2 * np.random.randn(1)[0]
                        jitter2 = 0.2 * np.random.randn(1)[0]
                        sp[:, :, sp_idx] += jitter1
                        mp[:, :, 2 * sp_idx] += jitter1
                        mp[:, :, 2 * sp_idx + 1] += jitter2
                        lp[:, :, 2 * sp_idx] += jitter1
                        lp[:, :, 2 * sp_idx + 1] += jitter2

                lpc[s_idx, :] = np.ravel(lp)
                mpc[s_idx, :] = np.ravel(mp)
                spc[s_idx, :] = np.ravel(sp)

            n_available = s_idx + 1

        return lpc[0:n_available, :], mpc[0:n_available], spc[0:n_available]

    def _scan_patches(self, meta, scan, db, prep, mode):

        ps = {'region_1': {}, 'region_2': {}}

        sr = np.random.randint(3)
        orr = np.random.randint(4)
        volumes = meta.load_volumes_norm_aligned(db, scan, db.sizes[sr], orr)
        self._get_coordinates(volumes[0].shape)
        tdm_1 = volumes[7] * (volumes[8] <= self.td_th_1)
        tdm_2 = volumes[7] * (volumes[8] <= self.td_th_2)

        mask_1, mask_2 = [{}, {}]
        for c in db.classes:
            mask_1 = np.where((volumes[6] == c) * tdm_1)
            mask_2 = np.where((volumes[6] == c) * tdm_2)

            ps['region_1'][c] = {}
            ps['region_1'][c]['l_patch'], ps['region_1'][c]['m_patch'], ps['region_1'][c]['s_patch'] =\
                self._class_patches(db, scan, volumes, mask_1, prep, mode)

            ps['region_2'][c] = {}
            ps['region_2'][c]['l_patch'], ps['region_2'][c]['m_patch'], ps['region_2'][c]['s_patch'] =\
                self._class_patches(db, scan, volumes, mask_2, prep, mode)

        return ps

    def _shuffle_and_select_data(self, data_dict, c, db):
        c_min = min([c['r1'][k] for k in c['r1']])
        lp_data_r1 = np.zeros((db.n_classes * c_min,
                               self.lp_w * self.lp_h * self.lp_d))
        mp_data_r1 = np.zeros((db.n_classes * c_min,
                               self.mp_w * self.mp_h * self.mp_d))
        sp_data_r1 = np.zeros((db.n_classes * c_min,
                               self.sp_w * self.sp_h * self.sp_d))
        labels_r1 = np.zeros((db.n_classes * c_min, db.n_classes))

        for i, k in enumerate(c['r1'].keys()):
            p = np.arange(c['r1'][k])
            np.random.shuffle(p)
            lp_data_r1[i * c_min:(i + 1) * c_min, :] =\
                data_dict['region_1'][k]['l_patch'][p, :][0:c_min, :]
            mp_data_r1[i * c_min:(i + 1) * c_min, :] =\
                data_dict['region_1'][k]['m_patch'][p, :][0:c_min, :]
            sp_data_r1[i * c_min:(i + 1) * c_min, :] =\
                data_dict['region_1'][k]['s_patch'][p, :][0:c_min, :]
            labels_r1[i * c_min:(i + 1) * c_min, i] = 1

        c_min = min([c['r2'][k] for k in c['r2']])
        lp_data_r2 = np.zeros((db.n_classes * c_min, self.lp_w * self.lp_h * self.lp_d))
        mp_data_r2 = np.zeros((db.n_classes * c_min, self.mp_w * self.mp_h * self.mp_d))
        sp_data_r2 = np.zeros((db.n_classes * c_min, self.sp_w * self.sp_h * self.sp_d))
        labels_r2 = np.zeros((db.n_classes * c_min, db.n_classes))
        for i, k in enumerate(c['r2'].keys()):
            p = np.arange(c['r2'][k])
            np.random.shuffle(p)
            lp_data_r2[i * c_min:(i + 1) * c_min, :] =\
                data_dict['region_2'][k]['l_patch'][p, :][0:c_min, :]
            mp_data_r2[i * c_min:(i + 1) * c_min, :] =\
                data_dict['region_2'][k]['m_patch'][p, :][0:c_min, :]
            sp_data_r2[i * c_min:(i + 1) * c_min, :] =\
                data_dict['region_2'][k]['s_patch'][p, :][0:c_min, :]
            labels_r2[i * c_min:(i + 1) * c_min, i] = 1

        data_r1 = {}
        data_r1['l_patch'] = lp_data_r1
        data_r1['m_patch'] = mp_data_r1
        data_r1['s_patch'] = sp_data_r1
        data_r1['labels'] = labels_r1

        data_r2 = {}
        data_r2['l_patch'] = lp_data_r2
        data_r2['m_patch'] = mp_data_r2
        data_r2['s_patch'] = sp_data_r2
        data_r2['labels'] = labels_r2

        return data_r1, data_r2

    def extract_train_or_valid_data(self, db, meta, prep, exp_out, mode='train'):
        """Extraction of training data with augmentation."""
        """
            Arguments:
                db: DatabaseISLES object
                pp: PreprocessorISLES object
                exp_put: path to the experiment output
                mode: train, valid or train_test(without augmentation) mode
            Returns:
                data and corresponding labels
        """
        data = self._allocate_data_memory(db, mode)
        selected_scans = self._select_scans_randomly(db, mode)

        data_dict = db.train_dict
        c = {'r1': {}, 'r2': {}}
        for k in db.classes:
            c['r1'][k] = 0
            c['r2'][k] = 0

        for s_idx, s in enumerate(selected_scans):
            data_s = self._scan_patches(meta, data_dict[s], db, prep, mode)
            for i in db.classes:
                n = data_s['region_1'][i]['l_patch'].shape[0]
                data['region_1'][i]['l_patch'][c['r1'][i]:
                                               c['r1'][i] + n, :] =\
                    data_s['region_1'][i]['l_patch']
                data['region_1'][i]['m_patch'][c['r1'][i]:
                                               c['r1'][i] + n, :] =\
                    data_s['region_1'][i]['m_patch']
                data['region_1'][i]['s_patch'][c['r1'][i]:
                                               c['r1'][i] + n, :] =\
                    data_s['region_1'][i]['s_patch']
                c['r1'][i] += n
            for i in db.classes:
                n = data_s['region_2'][i]['l_patch'].shape[0]
                data['region_2'][i]['l_patch'][c['r2'][i]:
                                               c['r2'][i] + n, :] =\
                    data_s['region_2'][i]['l_patch']
                data['region_2'][i]['m_patch'][c['r2'][i]:
                                               c['r2'][i] + n, :] =\
                    data_s['region_2'][i]['m_patch']
                data['region_2'][i]['s_patch'][c['r2'][i]:
                                               c['r2'][i] + n, :] =\
                    data_s['region_2'][i]['s_patch']
                c['r2'][i] += n
        data_ = {}
        data_['region_1'], data_['region_2'] =\
            self._shuffle_and_select_data(data, c, db)

        if data_['region_1']['labels'].shape[0]:
            return data_
        else:
            return None

    def extract_test_patches(self, scan, db, pp, volumes, ind_part):
        """Extraction of test patches."""
        n_indices = len(ind_part[0])
        test_data = {}
        test_data['l_patch'] = np.zeros((n_indices,
                                         self.lp_h * self.lp_w * self.lp_d))
        test_data['m_patch'] = np.zeros((n_indices,
                                         self.mp_h * self.mp_w * self.mp_d))
        test_data['s_patch'] = np.zeros((n_indices,
                                         self.sp_h * self.sp_w * self.sp_d))

        lp = np.zeros((self.lp_h, self.lp_w, self.lp_d))
        mp = np.zeros((self.mp_h, self.mp_w, self.mp_d))
        sp = np.zeros((self.sp_h, self.sp_w, self.sp_d))
        for j in range(n_indices):
            b = [ind_part[0][j] - self.pvs, ind_part[0][j] + self.pve,
                 ind_part[1][j] - self.phs, ind_part[1][j] + self.phe,
                 ind_part[2][j]]
            for i, m in enumerate(db.modalities[:-1]):
                lpm, mpm, spm = self._modality_patches(scan, m, volumes[i], b, 'test')
                lp[:, :, i * self.lpm_d:(i + 1) * self.lpm_d] = lpm
                mp[:, :, i * self.mpm_d:(i + 1) * self.mpm_d] = mpm
                sp[:, :, i * self.spm_d:(i + 1) * self.spm_d] = spm
            lp[:, :, self.lpm_d * db.n_modalities: (self.lpm_d * db.n_modalities + 2)] =\
                self._extract_distances_for_point(b, volumes[0].shape)

            test_data['l_patch'][j, :] = np.ravel(lp)
            test_data['m_patch'][j, :] = np.ravel(mp)
            test_data['s_patch'][j, :] = np.ravel(sp)
        return test_data

    def name(self):
        """Class name reproduction."""
        """
            Returns patch_extractor's name.
        """
        return ("%s(lp_w=%s, lp_h=%s, lp_d=%s, "
                   "mp_w=%s, mp_h=%s, mp_d=%s, "
                   "sp_w=%s, sp_h=%s, sp_d=%s, "
                   "td_th_1=%s, td_th_2=%s)"
                % (type(self).__name__,
                   self.lp_w, self.lp_h, self.lp_d,
                   self.mp_w, self.mp_h, self.mp_d,
                   self.sp_w, self.sp_h, self.sp_d,
                   self.td_th_1, self.td_th_2))

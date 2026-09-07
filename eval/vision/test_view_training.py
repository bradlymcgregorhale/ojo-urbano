"""Checks for partial crop supervision and photo-level view aggregation."""
import unittest

import numpy as np
from scipy.optimize import check_grad

from eval.vision.view_training import (
    _loss_gradient, fit_view_classifier, predict_view_classifiers, view_features,
)


class ViewTrainingTests(unittest.TestCase):
    def test_gradient_with_unknown_crops_and_mask(self):
        rng = np.random.default_rng(7)
        x = rng.normal(size=(5, 3, 4))
        mask = np.array([[1, 1, 0], [1, 0, 0], [1, 1, 1], [1, 1, 0], [1, 1, 1]], bool)
        y = np.array([1, 0, 1, 0, 1])
        crops = np.array([[1, np.nan, np.nan], [0, np.nan, np.nan],
                          [np.nan, 1, 0], [0, 0, np.nan], [1, np.nan, 1]])
        theta = rng.normal(size=5)
        args = (x, mask, y, 2, crops, .7)
        error = check_grad(lambda t: _loss_gradient(t, *args)[0],
                           lambda t: _loss_gradient(t, *args)[1], theta)
        self.assertLess(error, 1e-5)

    def test_padded_views_cannot_change_fit_or_prediction(self):
        x = np.array([[[-2.], [-1.]], [[1.], [2.]], [[-3.], [-2.]], [[2.], [3.]]])
        mask = np.ones((4, 2), bool)
        y = np.array([0, 1, 0, 1])
        padded = np.concatenate([x, np.full((4, 1, 1), 1e5)], axis=1)
        padded_mask = np.column_stack([mask, np.zeros(4, bool)])
        one = fit_view_classifier(x, mask, y)
        two = fit_view_classifier(padded, padded_mask, y)
        np.testing.assert_allclose(one, two, atol=1e-8)
        np.testing.assert_allclose(predict_view_classifiers(x, mask, [one]),
                                   predict_view_classifiers(padded, padded_mask, [two]), atol=1e-8)

    def test_photo_positive_can_be_in_any_view(self):
        x = np.array([[[-2.], [2.]], [[3.], [-3.]], [[-2.], [-1.]], [[-3.], [-2.]]])
        mask = np.ones((4, 2), bool)
        y = np.array([1, 1, 0, 0])
        crops = np.array([[0, 1], [1, 0], [0, 0], [0, 0]], float)
        weights = fit_view_classifier(x, mask, y, c=10, crop_targets=crops)
        result = predict_view_classifiers(x, mask, [weights])[:, 0]
        np.testing.assert_array_equal(result >= .5, y)
        np.testing.assert_allclose(result,
                                   predict_view_classifiers(x[:, ::-1], mask, [weights])[:, 0])

    def test_unknown_crop_labels_add_no_supervision(self):
        x = np.arange(12, dtype=float).reshape(3, 2, 2) / 10
        mask = np.ones((3, 2), bool)
        y = np.array([0, 0, 1])
        np.testing.assert_allclose(
            fit_view_classifier(x, mask, y),
            fit_view_classifier(x, mask, y, crop_targets=np.full((3, 2), np.nan)),
        )

    def test_invalid_or_empty_views_fail(self):
        x = np.ones((2, 2, 3))
        mask = np.ones((2, 2), bool)
        with self.assertRaises(ValueError):
            fit_view_classifier(x, np.zeros_like(mask), [0, 1])
        with self.assertRaises(ValueError):
            fit_view_classifier(x, mask, [0, 1], crop_targets=np.full((2, 2), np.inf))
        with self.assertRaises(ValueError):
            fit_view_classifier(x, mask, [0, .5])
        with self.assertRaises(ValueError):
            predict_view_classifiers(x, mask, [[1, 2]])

    def test_feature_transform_is_finite_and_preserves_inputs(self):
        x = np.ones((2, 3, 4))
        p = np.array([0., .5, 1.])[None, None, :].repeat(2, 0).repeat(3, 1)
        xx, pp = x.copy(), p.copy()
        result = view_features(x, p)
        self.assertEqual(result.shape, (2, 3, 7))
        self.assertTrue(np.isfinite(result).all())
        np.testing.assert_array_equal(x, xx)
        np.testing.assert_array_equal(p, pp)


if __name__ == '__main__':
    unittest.main()

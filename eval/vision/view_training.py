"""Train small classifiers on whole-photo and object-view feature caches."""
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit, logsumexp


def view_features(image_features, original_probabilities):
    """Combine frozen image embeddings with the original classifier's scores."""
    features = np.asarray(image_features, dtype=np.float64)
    probabilities = np.asarray(original_probabilities, dtype=np.float64)
    if (features.ndim != 3 or probabilities.ndim != 3
            or features.shape[:2] != probabilities.shape[:2]
            or not all(np.isfinite(a).all() for a in (features, probabilities))
            or (probabilities < 0).any() or (probabilities > 1).any()):
        raise ValueError('Expected finite aligned view features and probabilities.')
    return np.concatenate([
        features * 10,
        logit(np.clip(probabilities, 1e-5, 1 - 1e-5)) * .3,
    ], axis=2)


def _views(features, mask):
    x = np.asarray(features, dtype=np.float64)
    valid = np.asarray(mask)
    if (x.ndim != 3 or not all(x.shape) or valid.shape != x.shape[:2]
            or valid.dtype != np.bool_ or not valid.any(axis=1).all()
            or not np.isfinite(x).all()):
        raise ValueError('Every photo needs finite features and at least one valid view.')
    return x, valid


def _loss_gradient(theta, features, mask, targets, c, crop_targets=None, crop_weight=1):
    """Smooth maximum per photo with optional partial crop supervision."""
    flat = features.reshape(-1, features.shape[-1])
    logits = (flat @ theta[:-1]).reshape(mask.shape) + theta[-1]
    valid_logits = np.where(mask, logits, -np.inf)
    normalizer = logsumexp(valid_logits, axis=1)
    score = normalizer - np.log(mask.sum(axis=1))
    residual = expit(score) - targets
    derivative = (np.exp(valid_logits - normalizer[:, None]) * residual[:, None]).reshape(-1)
    loss = np.sum(np.logaddexp(0, score) - targets * score)
    loss += .5 / c * np.dot(theta[:-1], theta[:-1])
    gradient = np.r_[flat.T @ derivative + theta[:-1] / c, residual.sum()]
    if crop_targets is not None:
        supervised = mask & np.isfinite(crop_targets)
        values = np.nan_to_num(crop_targets)
        weights = crop_weight * supervised / np.maximum(supervised.sum(axis=1), 1)[:, None]
        loss += np.sum(weights * (np.logaddexp(0, logits) - values * logits))
        derivative = (weights * (expit(logits) - values)).reshape(-1)
        gradient += np.r_[flat.T @ derivative, derivative.sum()]
    return float(loss), gradient


def fit_view_classifier(features, mask, targets, c=1, crop_targets=None,
                        crop_weight=1, max_iter=500):
    """Fit one container type without assuming which crop carries a photo label.

    Targets belong to photos. Optional crop targets use NaN for unknown labels.
    The caller must exclude every view and crop label of validation photos from
    this fit. This helper doesn't choose splits, publish weights or modify the
    original classifier.
    """
    x, valid = _views(features, mask)
    y = np.asarray(targets, dtype=np.float64)
    if (y.shape != (len(x),) or not np.isfinite(y).all()
            or ((y != 0) & (y != 1)).any()
            or not np.isfinite(c) or c <= 0
            or not np.isfinite(crop_weight) or crop_weight < 0
            or not isinstance(max_iter, int) or max_iter < 1):
        raise ValueError('Expected binary photo targets and valid fitting parameters.')
    crops = None
    if crop_targets is not None:
        crops = np.asarray(crop_targets, dtype=np.float64)
        if (crops.shape != valid.shape or np.isinf(crops).any()
                or ((crops[np.isfinite(crops)] != 0) & (crops[np.isfinite(crops)] != 1)).any()):
            raise ValueError('Crop targets must be binary or unknown (NaN).')
    start = np.zeros(x.shape[-1] + 1)
    start[-1] = logit((y.sum() + .5) / (len(y) + 1))
    result = minimize(
        _loss_gradient, start, args=(x, valid, y, c, crops, crop_weight),
        jac=True, method='L-BFGS-B',
        options={'maxiter': max_iter, 'ftol': 1e-9, 'gtol': 1e-5},
    )
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError('View classifier failed to converge: ' + str(result.message))
    return result.x


def predict_view_classifiers(features, mask, classifiers):
    """Return one probability per photo and type, ignoring padded views."""
    x, valid = _views(features, mask)
    classifiers = np.asarray(classifiers, dtype=np.float64)
    if (classifiers.ndim != 2 or not len(classifiers)
            or classifiers.shape[1] != x.shape[-1] + 1
            or not np.isfinite(classifiers).all()):
        raise ValueError('Classifier weights must match the view features.')
    probabilities = []
    for theta in classifiers:
        logits = x @ theta[:-1] + theta[-1]
        logits = np.where(valid, logits, -np.inf)
        probabilities.append(expit(logsumexp(logits, axis=1) - np.log(valid.sum(axis=1))))
    return np.column_stack(probabilities)

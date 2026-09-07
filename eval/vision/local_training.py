"""Local classifier updates and complete labels, without API calls."""
import copy
from contextlib import closing
import sqlite3
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

CONTAINER_TYPES = (
    'contenedor_secos',
    'contenedor_humedos_lateral',
    'contenedor_humedos_bilateral',
)


def refine_container_heads(bundle, reference_features, reviewed_features,
                           reviewed_labels, review_weight=1.0):
    """Learn explicit container labels while retaining the current model's scores.

    Start from the current bundle, including earlier corrections, rather than
    rebuilding its heads from historical labels. Reference photos must exclude
    validation photos. Callers must check photo/scene overlap before fitting and
    evaluate the returned candidate separately; this function never publishes it.
    """
    pipeline = bundle['clf']
    scaler = pipeline.named_steps['standardscaler']
    heads = pipeline.named_steps['onevsrestclassifier'].estimators_
    classes = list(bundle['classes'])
    indices = [classes.index(key) for key in CONTAINER_TYPES]
    references = np.asarray(reference_features)
    reviewed = np.asarray(reviewed_features)
    width = heads[indices[0]].coef_.shape[1]
    for matrix in (references, reviewed):
        if (matrix.ndim != 2 or not len(matrix) or matrix.shape[1] != width
                or not np.issubdtype(matrix.dtype, np.number)
                or not np.isfinite(matrix).all()):
            raise ValueError('Expected nonempty, finite image feature matrices.')
    labels = list(reviewed_labels)
    if (len(labels) != len(reviewed)
            or any(not isinstance(row, dict) or set(row) != set(CONTAINER_TYPES)
                   or any(type(value) is not bool for value in row.values())
                   for row in labels)):
        raise ValueError('Every photo needs explicit booleans for all three container types.')
    if not np.isfinite(review_weight) or review_weight <= 0:
        raise ValueError('Review weight must be finite and positive.')
    reference_x = scaler.transform(references).astype(np.float64)
    reviewed_x = scaler.transform(reviewed).astype(np.float64)
    features = np.vstack([reference_x, reviewed_x])
    weights = np.r_[np.ones(len(references)), np.full(len(reviewed), review_weight)]
    original_scores = pipeline.predict_proba(references)
    candidate = copy.deepcopy(bundle)
    candidate_heads = candidate['clf'].named_steps['onevsrestclassifier'].estimators_
    fits = {}
    for key, index in zip(CONTAINER_TYPES, indices):
        targets = np.r_[original_scores[:, index], [row[key] for row in labels]]
        candidate_heads[index], fits[key] = refine_head(
            heads[index], features, targets, weights)
    return candidate, fits


def read_multilabel_tags(database):
    """Use every assigned tag, never the single primary label as an absence."""
    tags = {}
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as con:
        for photo, category in con.execute('SELECT file, label_type FROM photo_tags'):
            tags.setdefault(photo, set()).add(category)
    return tags


def refine_head(head, features, targets, weights, max_iter=1500):
    """Fit reviewed corrections and retention targets around the existing weights.

    Features must use the existing scaler. Retention targets are the original
    head's scores on reference photos; corrected examples supply human labels.
    This regularization limits drift but doesn't replace regression validation.
    """
    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if (x.ndim != 2 or y.shape != (len(x),) or w.shape != y.shape
            or x.shape[1] != head.coef_.shape[1] or len(head.coef_) != 1
            or not all(np.isfinite(a).all() for a in (x, y, w))
            or (y < 0).any() or (y > 1).any() or (w <= 0).any()):
        raise ValueError('Invalid binary-head training inputs.')
    original = np.r_[head.coef_[0], head.intercept_[0]].astype(np.float64)

    def objective(theta):
        scores = x @ theta[:-1] + theta[-1]
        delta = theta - original
        residual = (expit(scores) - y) * w
        loss = np.dot(w, np.logaddexp(0, scores) - y * scores) + .5 * np.dot(delta, delta)
        gradient = np.r_[x.T @ residual, residual.sum()] + delta
        return loss, gradient

    result = minimize(objective, original, method='L-BFGS-B', jac=True,
                      options={'maxiter': max_iter, 'ftol': 1e-11, 'gtol': 1e-5})
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError('Head update failed to converge: ' + str(result.message))
    updated = copy.deepcopy(head)
    updated.coef_ = result.x[:-1].reshape(1, -1)
    updated.intercept_ = result.x[-1:]
    return updated, {'iterations': int(result.nit), 'objective': float(result.fun)}

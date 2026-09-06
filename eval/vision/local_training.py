"""Local classifier updates and complete labels, without API calls."""
import copy
from contextlib import closing
import sqlite3
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit


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

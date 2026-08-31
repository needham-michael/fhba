import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from typing import Tuple, Dict

from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC

from fhba.classification.utils import prep_inputs

def rforest(
    ds : xr.Dataset,
    userpts : gpd.GeoDataFrame,
    n_estimators: int = 200,
    n_jobs: int = -1, 
    random_state: int = 8675309,
    **clf_kwargs
) -> Tuple[xr.DataArray,xr.DataArray]:

    inp = prep_inputs(ds=ds,userpts=userpts)
    method_defaults = dict(n_estimators=n_estimators,n_jobs=n_jobs,random_state=random_state)
    method_defaults.update(clf_kwargs)

    X_train = pd.concat([inp.Xbrn, inp.Xunb], ignore_index=True)
    y_train = np.array([1] * len(inp.Xbrn) + [0] * len(inp.Xunb))

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    Xfull_scaled   = scaler.transform(inp.Xfull)

    # --------------------------------------------------------
    classifier = RandomForestClassifier(**method_defaults)
    classifier.fit(X_train_scaled,y_train)

    y_pred = classifier.predict(Xfull_scaled)
    class_idx = list(classifier.classes_).index(1)
    confidence_vals = classifier.predict_proba(Xfull_scaled)[:, class_idx]

    is_burned = xr.DataArray(y_pred.astype(bool),coords={'z': inp.pixel_vector_1d.z}).unstack()
    confidence = xr.DataArray(confidence_vals, coords={'z': inp.pixel_vector_1d.z}).unstack()

    return is_burned, confidence

def svm(
    ds : xr.Dataset,
    userpts : gpd.GeoDataFrame,
    kernel: str='rbf',
    C: float=1.0,
    probability: bool=False,
    **clf_kwargs
) -> Tuple[xr.DataArray,xr.DataArray]:

    inp = prep_inputs(ds=ds,userpts=userpts)
    method_defaults = dict(kernel=kernel,C=C,probability=probability)
    method_defaults.update(clf_kwargs)

    X_train = pd.concat([inp.Xbrn, inp.Xunb], ignore_index=True)
    y_train = np.array([1] * len(inp.Xbrn) + [0] * len(inp.Xunb))

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    Xfull_scaled   = scaler.transform(inp.Xfull)

    # --------------------------------------------------------
    classifier = SVC(**method_defaults)
    classifier.fit(X_train_scaled,y_train)
    y_pred = classifier.predict(Xfull_scaled)
    confidence_vals = classifier.decision_function(Xfull_scaled)

    is_burned = xr.DataArray(y_pred.astype(bool),coords={'z': inp.pixel_vector_1d.z}).unstack()
    confidence_da = xr.DataArray(confidence_vals, coords={'z': inp.pixel_vector_1d.z}).unstack()

    return is_burned, confidence_da
    

    


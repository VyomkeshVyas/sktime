# copyright: sktime developers, BSD-3-Clause License (see LICENSE file)
"""Implements adapter for StatsForecast models."""
import pandas

from sktime.forecasting.base import BaseForecaster

__all__ = ["_GeneralisedStatsForecastAdapter"]
__author__ = ["yarnabrina"]


class _GeneralisedStatsForecastAdapter(BaseForecaster):
    """Base adapter class for StatsForecast models."""

    _tags = {
        "y_inner_mtype": "pd.Series",
        "X_inner_mtype": "pd.DataFrame",
        "scitype:y": "univariate",
        "requires-fh-in-fit": False,
        # "X-y-must-have-same-index": True,  # TODO: need to check (how?)
        # "enforce_index_type": None,  # TODO: need to check (how?)
        "handles-missing-data": False,
        "python_version": ">=3.8",
        "python_dependencies": ["statsforecast"],
    }

    def __init__(self):
        super().__init__()

        self._forecaster = None

    def _instantiate_model(self):
        raise NotImplementedError("abstract method")

    def _fit(self, y, X, fh):
        """Fit forecaster to training data.

        private _fit containing the core logic, called from fit

        Writes to self:
            Sets fitted model attributes ending in "_".

        Parameters
        ----------
        y : sktime time series object
            guaranteed to be of an mtype in self.get_tag("y_inner_mtype")
            Time series to which to fit the forecaster.
            if self.get_tag("scitype:y")=="univariate":
                guaranteed to have a single column/variable
            if self.get_tag("scitype:y")=="multivariate":
                guaranteed to have 2 or more columns
            if self.get_tag("scitype:y")=="both": no restrictions apply
        fh : guaranteed to be ForecastingHorizon or None, optional (default=None)
            The forecasting horizon with the steps ahead to to predict.
            Required (non-optional) here if self.get_tag("requires-fh-in-fit")==True
            Otherwise, if not passed in _fit, guaranteed to be passed in _predict
        X :  sktime time series object, optional (default=None)
            guaranteed to be of an mtype in self.get_tag("X_inner_mtype")
            Exogeneous time series to fit to.

        Returns
        -------
        self : reference to self
        """
        del fh  # avoid being detected as unused by ``vulture`` like tools

        self._forecaster = self._instantiate_model()

        y_fit_input = y.to_numpy(copy=False)

        X_fit_input = X
        if X_fit_input is not None:
            X_fit_input = X.to_numpy(copy=False)

        self._forecaster.fit(y_fit_input, X=X_fit_input)

        return self

    def _predict_in_or_out_of_sample(
        self, fh, fh_type, X=None, levels=None, legacy_interface=True
    ):
        maximum_forecast_horizon = fh.to_relative(self.cutoff)[-1]

        absolute_horizons = fh.to_absolute_index(self.cutoff)
        horizon_positions = fh.to_indexer(self.cutoff)

        level_arguments = None if levels is None else [100 * level for level in levels]

        if fh_type == "in-sample":
            predictions = self._forecaster.predict_in_sample(level=level_arguments)
            point_predictions = predictions["fitted"]
        elif fh_type == "out-of-sample":
            predictions = self._forecaster.predict(
                maximum_forecast_horizon, X=X, level=level_arguments
            )
            point_predictions = predictions["mean"]

        if isinstance(point_predictions, pandas.Series):
            point_predictions = point_predictions.to_numpy()

        final_point_predictions = pandas.Series(
            point_predictions[horizon_positions], index=absolute_horizons
        )

        if levels is None:
            return final_point_predictions

        var_names = self._get_varnames(
            default="Coverage", legacy_interface=legacy_interface
        )
        var_name = var_names[0]

        interval_predictions_indices = pandas.MultiIndex.from_product(
            [var_names, levels, ["lower", "upper"]]
        )
        interval_predictions = pandas.DataFrame(
            index=absolute_horizons, columns=interval_predictions_indices
        )

        if fh_type == "out-of-sample":
            column_prefix = ""
        elif fh_type == "in-sample":
            column_prefix = "fitted-"

        for level, level_argument in zip(levels, level_arguments):
            lower_interval_predictions = predictions[
                f"{column_prefix}lo-{level_argument}"
            ]
            if isinstance(lower_interval_predictions, pandas.Series):
                lower_interval_predictions = lower_interval_predictions.to_numpy()

            upper_interval_predictions = predictions[
                f"{column_prefix}hi-{level_argument}"
            ]
            if isinstance(upper_interval_predictions, pandas.Series):
                upper_interval_predictions = upper_interval_predictions.to_numpy()

            interval_predictions[
                (var_name, level, "lower")
            ] = lower_interval_predictions[horizon_positions]
            interval_predictions[
                (var_name, level, "upper")
            ] = upper_interval_predictions[horizon_positions]

        return interval_predictions

    def _split_horizon(self, fh):
        in_sample_horizon = fh.to_in_sample(self.cutoff)
        out_of_sample_horizon = fh.to_out_of_sample(self.cutoff)

        return in_sample_horizon, out_of_sample_horizon

    def _predict(self, fh, X):
        """Forecast time series at future horizon.

        private _predict containing the core logic, called from predict

        State required:
            Requires state to be "fitted".

        Accesses in self:
            Fitted model attributes ending in "_"
            self.cutoff

        Parameters
        ----------
        fh : guaranteed to be ForecastingHorizon or None, optional (default=None)
            The forecasting horizon with the steps ahead to to predict.
            If not passed in _fit, guaranteed to be passed here
        X : sktime time series object, optional (default=None)
            guaranteed to be of an mtype in self.get_tag("X_inner_mtype")
            Exogeneous time series for the forecast

        Returns
        -------
        y_pred : sktime time series object
            should be of the same type as seen in _fit, as in "y_inner_mtype" tag
            Point predictions
        """
        X_predict_input = X.to_numpy(copy=False) if X is not None else X

        in_sample_horizon, out_of_sample_horizon = self._split_horizon(fh)

        point_predictions = []

        if in_sample_horizon:
            in_sample_point_predictions = self._predict_in_or_out_of_sample(
                in_sample_horizon, "in-sample"
            )
            point_predictions.append(in_sample_point_predictions)

        if out_of_sample_horizon:
            out_of_sample_point_predictions = self._predict_in_or_out_of_sample(
                out_of_sample_horizon, "out-of-sample", X=X_predict_input
            )
            point_predictions.append(out_of_sample_point_predictions)

        final_point_predictions = pandas.concat(point_predictions, copy=False)
        final_point_predictions.name = self._y.name

        return final_point_predictions

    # todo 0.22.0 - switch legacy_interface default to False
    # todo 0.23.0 - remove legacy_interface arg and logic using it
    def _predict_interval(self, fh, X, coverage, legacy_interface=True):
        """Compute/return prediction quantiles for a forecast.

        private _predict_interval containing the core logic,
            called from predict_interval and possibly predict_quantiles

        State required:
            Requires state to be "fitted".

        Accesses in self:
            Fitted model attributes ending in "_"
            self.cutoff

        Parameters
        ----------
        fh : guaranteed to be ForecastingHorizon
            The forecasting horizon with the steps ahead to to predict.
        X :  sktime time series object, optional (default=None)
            guaranteed to be of an mtype in self.get_tag("X_inner_mtype")
            Exogeneous time series for the forecast
        coverage : list of float (guaranteed not None and floats in [0,1] interval)
           nominal coverage(s) of predictive interval(s)

        Returns
        -------
        pred_int : pd.DataFrame
            Column has multi-index: first level is variable name from y in fit,
                second level coverage fractions for which intervals were computed.
                    in the same order as in input `coverage`.
                Third level is string "lower" or "upper", for lower/upper interval end.
            Row index is fh, with additional (upper) levels equal to instance levels,
                from y seen in fit, if y_inner_mtype is Panel or Hierarchical.
            Entries are forecasts of lower/upper interval end,
                for var in col index, at nominal coverage in second col index,
                lower/upper depending on third col index, for the row index.
                Upper/lower interval end forecasts are equivalent to
                quantile forecasts at alpha = 0.5 - c/2, 0.5 + c/2 for c in coverage.
        """
        X_predict_input = X if X is None else X.to_numpy(copy=False)

        in_sample_horizon, out_of_sample_horizon = self._split_horizon(fh)

        interval_predictions = []

        if in_sample_horizon:
            in_sample_interval_predictions = self._predict_in_or_out_of_sample(
                in_sample_horizon,
                "in-sample",
                levels=coverage,
                legacy_interface=legacy_interface,
            )
            interval_predictions.append(in_sample_interval_predictions)

        if out_of_sample_horizon:
            out_of_sample_interval_predictions = self._predict_in_or_out_of_sample(
                out_of_sample_horizon,
                "out-of-sample",
                X=X_predict_input,
                levels=coverage,
                legacy_interface=legacy_interface,
            )
            interval_predictions.append(out_of_sample_interval_predictions)

        final_interval_predictions = pandas.concat(interval_predictions, copy=False)

        return final_interval_predictions


class StatsForecastBackAdapter:
    """StatsForecast Back Adapter.

    StatsForecastBackAdapter is a wrapper for sktime forecasters to be used in
    StatsForecast composite models.

    Parameters
    ----------
    estimator : sktime forecaster

    Examples
    --------
    >>> from sktime.datasets import load_airline
    >>> from sktime.forecasting.statsforecast import StatsForecastMSTL
    >>> from sktime.forecasting.ets import AutoETS

    >>> y = load_airline()
    >>> trend_forecaster = AutoETS() # doctest: +SKIP
    >>> model = StatsForecastMSTL( # doctest: +SKIP
            season_length=[3,12],
            trend_forecaster=trend_forecaster
        )
    >>> fitted_model = model.fit(y=y) # doctest: +SKIP
    >>> y_pred = fitted_model.predict(fh=[1,2,3]) # doctest: +SKIP
    """

    _tags = {
        "python_dependencies": ["statsforecast"],
    }

    def __init__(self, estimator):
        super().__init__()

        self.estimator = estimator

    def __repr__(self):
        return "StatsForecastBackAdapter"

    def fit(self, y, X=None):
        """Fit to training data.

        Parameters
        ----------
        y : ndarray
            Time series of shape (t, ) without missing values
        X : typing.Optional[numpy.ndarray], default=None

        Returns
        -------
        self : returns an instance of self.
        """
        self.estimator = self.estimator.fit(y=y, X=X)

        return self

    def predict(self, h, X=None, level=None):
        """Make forecasts.

        Parameters
        ----------
        h : int
            Forecast horizon.
        X : typing.Optional[numpy.ndarray], default=None
            Optional exogenous of shape (h, n_x).
        level : typing.Optional[typing.Tuple[int]], default=None
            Confidence levels (0-100) for prediction intervals.

        Returns
        -------
        y_pred : dict
            Dictionary with entries mean for point predictions and level_* for
            probabilistic predictions.
        """
        mean = self.estimator.predict(fh=range(1, h + 1), X=X)[:, 0]
        if level is None:
            return {"mean": mean}

        level = sorted(level)
        coverage = [round(1 - (_l / 100), 2) for _l in level]

        pred_int = self.estimator.predict_interval(
            fh=range(1, h + 1), X=X, coverage=coverage
        )

        return self.format_pred_int("mean", mean, pred_int, coverage, level)

    def predict_in_sample(self, level=None):
        """Access fitted MSTL insample predictions.

        Parameters
        ----------
        level : typing.Optional[typing.Tuple[int]]
            Confidence levels (0-100) for prediction intervals.

        Returns
        -------
        y_pred : dict
            Dictionary with entries mean for point predictions and level_* for
            probabilistic predictions.
        """
        fitted = self.estimator.predict(self.estimator._y.index)[:, 0]

        if level is None:
            return {"fitted": fitted}

        level = sorted(level)
        coverage = [round(1 - (_l / 100), 2) for _l in level]
        pred_int = self.estimator.predict_interval(
            fh=self.estimator._y.index, X=self.estimator._X, coverage=coverage
        )
        return self.format_pred_int("fitted", fitted, pred_int, coverage, level)

    def format_pred_int(self, y_pred_name, y_pred, pred_int, coverage, level):
        pred_int_prefix = "fitted-" if y_pred_name == "fitted" else ""

        return {
            y_pred_name: y_pred,
            **{
                f"{pred_int_prefix}lo-{_l}": pred_int[("Coverage", c, "lower")].values
                for c, _l in zip(reversed(coverage), reversed(level))
            },
            **{
                f"{pred_int_prefix}hi-{_l}": pred_int[("Coverage", c, "upper")].values
                for c, _l in zip(coverage, level)
            },
        }

    @classmethod
    def get_test_params(cls, parameter_set="default"):
        """Return testing parameter settings for the estimator.

        Parameters
        ----------
        parameter_set : str, default="default"
            Name of the set of test parameters to return, for use in tests. If no
            special parameters are defined for a value, will return `"default"` set.
            There are currently no reserved values for forecasters.

        Returns
        -------
        params : dict or list of dict, default = {}
            Parameters to create testing instances of the class
            Each dict are parameters to construct an "interesting" test instance, i.e.,
            `MyClass(**params)` or `MyClass(**params[i])` creates a valid test instance.
            `create_test_instance` uses the first (or only) dictionary in `params`
        """
        from sktime.utils.validation._dependencies import _check_soft_dependencies

        del parameter_set  # to avoid being detected as unused by ``vulture`` etc.

        if _check_soft_dependencies("statsmodels", severity="none"):
            from sktime.forecasting.theta import ThetaForecaster
            from sktime.forecasting.var import VAR

            params = [
                {
                    "estimator": ThetaForecaster(),
                },
                {
                    "estimator": VAR(),
                },
            ]
        else:
            from sktime.forecasting.naive import NaiveForecaster

            params = [
                {
                    "estimator": NaiveForecaster(),
                },
            ]

        return params
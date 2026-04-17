"""
AI Anomaly Detector — Compliance Data
Author: Muhammad Ammar Ahmed — Senior Test Automation Engineer

Uses statistical ML (Isolation Forest + Z-Score) to detect anomalies
in compliance data fetched from Predict360 GRC platform.
Integrated with the UiPath RPA compliance automation workflow.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from scipy import stats
import requests
import json
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ComplianceAnomalyDetector:
    """
    AI-powered anomaly detection for compliance data.
    Detects unusual patterns in: risk scores, alert frequencies,
    regulatory change volumes, and audit findings.
    """

    def __init__(self, contamination=0.05):
        self.contamination = contamination  # Expected % of anomalies
        self.iso_forest = IsolationForest(
            contamination=contamination,
            random_state=42,
            n_estimators=100
        )
        self.scaler = StandardScaler()
        self.is_fitted = False
        self.anomaly_log = []

    def fetch_compliance_data(self, api_url, token, days_back=90):
        """Fetch compliance metrics from Predict360 API."""
        logger.info(f"Fetching compliance data for last {days_back} days...")
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

        headers = {"Authorization": f"Bearer {token}"}
        params = {"startDate": start_date, "endDate": end_date, "pageSize": 1000}

        response = requests.get(f"{api_url}/api/v1/compliance/metrics", headers=headers, params=params)
        response.raise_for_status()
        return pd.DataFrame(response.json().get('data', []))

    def prepare_features(self, df):
        """Extract and engineer features for anomaly detection."""
        features = pd.DataFrame()

        # Risk score features
        if 'riskScore' in df.columns:
            features['risk_score'] = pd.to_numeric(df['riskScore'], errors='coerce')
            features['risk_score_7d_avg'] = features['risk_score'].rolling(7, min_periods=1).mean()
            features['risk_score_std'] = features['risk_score'].rolling(7, min_periods=1).std().fillna(0)

        # Alert volume features
        if 'alertCount' in df.columns:
            features['alert_count'] = pd.to_numeric(df['alertCount'], errors='coerce')
            features['alert_velocity'] = features['alert_count'].diff().fillna(0)

        # Overdue items
        if 'overdueCount' in df.columns:
            features['overdue_ratio'] = (
                pd.to_numeric(df['overdueCount'], errors='coerce') /
                pd.to_numeric(df.get('totalCount', 1), errors='coerce').replace(0, 1)
            )

        return features.fillna(0)

    def train(self, df):
        """Train the anomaly detection model on historical compliance data."""
        logger.info("Training anomaly detection model...")
        features = self.prepare_features(df)

        if features.empty:
            raise ValueError("No features could be extracted from the data.")

        X_scaled = self.scaler.fit_transform(features)
        self.iso_forest.fit(X_scaled)
        self.is_fitted = True
        self.feature_columns = features.columns.tolist()
        logger.info(f"Model trained on {len(features)} records with {len(self.feature_columns)} features.")

    def detect(self, df):
        """
        Detect anomalies in new compliance data.
        Returns DataFrame with anomaly scores and flags.
        """
        if not self.is_fitted:
            raise RuntimeError("Model not trained. Call train() first.")

        features = self.prepare_features(df)
        X_scaled = self.scaler.transform(features[self.feature_columns])

        # Isolation Forest scores (-1 = anomaly, 1 = normal)
        iso_predictions = self.iso_forest.predict(X_scaled)
        iso_scores = self.iso_forest.score_samples(X_scaled)

        # Z-Score analysis for additional validation
        z_scores = np.abs(stats.zscore(X_scaled, axis=0))
        z_max = z_scores.max(axis=1)

        results = df.copy()
        results['anomaly_flag'] = iso_predictions == -1
        results['anomaly_score'] = -iso_scores  # Higher = more anomalous
        results['z_score_max'] = z_max
        results['severity'] = results.apply(self._classify_severity, axis=1)
        results['detected_at'] = datetime.now().isoformat()

        anomalies = results[results['anomaly_flag']]
        logger.info(f"Detected {len(anomalies)} anomalies out of {len(df)} records ({len(anomalies)/len(df)*100:.1f}%)")

        self.anomaly_log.extend(anomalies.to_dict('records'))
        return results

    def _classify_severity(self, row):
        """Classify anomaly severity based on score and z-score."""
        if not row.get('anomaly_flag', False):
            return 'Normal'
        score = row.get('anomaly_score', 0)
        z = row.get('z_score_max', 0)
        if score > 0.7 or z > 3.5:
            return 'Critical'
        elif score > 0.5 or z > 2.5:
            return 'High'
        elif score > 0.3 or z > 2.0:
            return 'Medium'
        return 'Low'

    def generate_report(self, results):
        """Generate a human-readable anomaly detection report."""
        anomalies = results[results['anomaly_flag']]
        total = len(results)
        report = {
            "generated_at": datetime.now().isoformat(),
            "total_records": total,
            "anomalies_detected": len(anomalies),
            "anomaly_rate": f"{len(anomalies)/total*100:.1f}%",
            "severity_breakdown": anomalies['severity'].value_counts().to_dict(),
            "top_anomalies": anomalies.nlargest(10, 'anomaly_score')[
                ['anomaly_score', 'severity', 'detected_at']
            ].to_dict('records')
        }
        logger.info(f"Report: {len(anomalies)} anomalies ({report['anomaly_rate']})")
        return report

    def alert_on_critical(self, results, webhook_url=None):
        """Send alerts for critical anomalies via webhook."""
        critical = results[results['severity'] == 'Critical']
        if critical.empty:
            logger.info("No critical anomalies detected.")
            return

        alert = {
            "title": f"CRITICAL: {len(critical)} compliance anomalies detected",
            "count": len(critical),
            "timestamp": datetime.now().isoformat(),
            "source": "AI Anomaly Detector — Predict360 GRC"
        }

        if webhook_url:
            try:
                requests.post(webhook_url, json=alert, timeout=10)
                logger.info(f"Alert sent to webhook: {len(critical)} critical anomalies")
            except Exception as e:
                logger.error(f"Failed to send webhook alert: {e}")
        else:
            logger.warning(f"CRITICAL ANOMALIES DETECTED: {json.dumps(alert, indent=2)}")

        return alert


if __name__ == "__main__":
    detector = ComplianceAnomalyDetector(contamination=0.05)
    logger.info("AI Compliance Anomaly Detector initialized.")
    logger.info("Usage: detector.train(historical_df) -> detector.detect(new_df)")

"""
Load test script for CVDash API.

Usage:
    pip install locust
    locust -f tests/locustfile.py --host http://localhost:8000

Then open http://localhost:8089 to configure and start the test.

For headless mode:
    locust -f tests/locustfile.py --host http://localhost:8000 \
        --users 50 --spawn-rate 5 --run-time 60s --headless

Requires a running backend with a seeded database (scripts/seed.py).
Uses the demo user: demo@cvdash.dev / Password1
"""

import os
from locust import HttpUser, task, between, events


# Default test credentials (from seed.py)
TEST_EMAIL = os.environ.get("LOCUST_EMAIL", "demo@cvdash.dev")
TEST_PASSWORD = os.environ.get("LOCUST_PASSWORD", "Password1")


class CVDashUser(HttpUser):
    """
    Simulates a researcher using the CVDash platform.

    Workflow:
    1. Login to get JWT
    2. Browse projects and analyses
    3. View results table with various filters
    4. Export epitopes
    5. Check analysis status
    6. View epitope detail / explainability
    """

    wait_time = between(1, 3)  # 1-3 seconds between requests

    def on_start(self):
        """Login and store JWT token."""
        response = self.client.post("/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        if response.status_code == 200:
            data = response.json()
            self.token = data.get("access_token", "")
            self.headers = {"Authorization": f"Bearer {self.token}"}
            # Store first analysis ID for later use
            self._fetch_first_analysis()
        else:
            self.token = ""
            self.headers = {}
            self.analysis_id = None

    def _fetch_first_analysis(self):
        """Get the first available analysis ID for subsequent requests."""
        resp = self.client.get(
            "/api/analyses?limit=1",
            headers=self.headers,
            name="/api/analyses (bootstrap)",
        )
        if resp.status_code == 200:
            data = resp.json()
            analyses = data.get("analyses", [])
            self.analysis_id = analyses[0]["id"] if analyses else None
        else:
            self.analysis_id = None

    # -- Auth endpoints --

    @task(1)
    def get_me(self):
        """Check current user profile."""
        self.client.get("/api/auth/me", headers=self.headers)

    # -- Health --

    @task(1)
    def health_check(self):
        """Shallow health check."""
        self.client.get("/health")

    # -- Projects --

    @task(3)
    def list_projects(self):
        """List all projects."""
        self.client.get("/api/projects?skip=0&limit=20", headers=self.headers)

    # -- Analyses --

    @task(5)
    def list_analyses(self):
        """List analyses with pagination."""
        self.client.get("/api/analyses?skip=0&limit=20", headers=self.headers)

    @task(2)
    def dashboard_stats(self):
        """Fetch dashboard aggregate stats."""
        self.client.get("/api/analyses/stats/dashboard", headers=self.headers)

    @task(3)
    def analysis_status(self):
        """Check analysis status (frequent polling pattern)."""
        if self.analysis_id:
            self.client.get(
                f"/api/analyses/{self.analysis_id}/status",
                headers=self.headers,
                name="/api/analyses/[id]/status",
            )

    # -- Epitopes (heaviest read path) --

    @task(10)
    def list_epitopes(self):
        """List epitopes for an analysis -- the most common read."""
        if self.analysis_id:
            self.client.get(
                f"/api/analyses/{self.analysis_id}/epitopes?skip=0&limit=50&sort_by=rank&sort_order=asc",
                headers=self.headers,
                name="/api/analyses/[id]/epitopes",
            )

    @task(3)
    def list_epitopes_filtered(self):
        """List epitopes with gene filter."""
        if self.analysis_id:
            self.client.get(
                f"/api/analyses/{self.analysis_id}/epitopes?gene=BRAF&min_score=0.5&limit=50",
                headers=self.headers,
                name="/api/analyses/[id]/epitopes?filtered",
            )

    @task(2)
    def filter_options(self):
        """Fetch filter dropdown values."""
        if self.analysis_id:
            self.client.get(
                f"/api/analyses/{self.analysis_id}/epitopes/filter-options",
                headers=self.headers,
                name="/api/analyses/[id]/epitopes/filter-options",
            )

    @task(1)
    def export_csv(self):
        """Export epitopes as CSV."""
        if self.analysis_id:
            self.client.get(
                f"/api/analyses/{self.analysis_id}/epitopes/export?format=csv",
                headers=self.headers,
                name="/api/analyses/[id]/epitopes/export",
            )

    # -- Genome browser --

    @task(2)
    def browser_tracks(self):
        """Fetch genome browser track data."""
        if self.analysis_id:
            self.client.get(
                f"/api/analyses/{self.analysis_id}/browser/tracks",
                headers=self.headers,
                name="/api/analyses/[id]/browser/tracks",
            )


class CVDashAdminUser(HttpUser):
    """
    Simulates heavier admin-like operations at lower frequency.
    Uses the same demo credentials.
    """

    wait_time = between(5, 15)
    weight = 1  # 1/10th the spawn rate of regular users

    def on_start(self):
        response = self.client.post("/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        if response.status_code == 200:
            self.token = response.json().get("access_token", "")
            self.headers = {"Authorization": f"Bearer {self.token}"}
        else:
            self.token = ""
            self.headers = {}

    @task(1)
    def deep_health(self):
        """Deep health check (DB + Redis + Celery)."""
        self.client.get("/health/deep")

    @task(1)
    def export_my_data(self):
        """GDPR data export."""
        self.client.get("/api/auth/export-my-data", headers=self.headers)

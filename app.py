"""Application entry point for the TfL bus-route explorer."""

import streamlit as st

from route_data import OFFLINE_SNAPSHOT_PATH, OFFLINE_STATS_PATH


def main() -> None:
    """Configure the app and run the selected page."""
    st.set_page_config(page_title="TfL bus routes", page_icon="🚌", layout="wide")
    offline_available = OFFLINE_SNAPSHOT_PATH.exists() and OFFLINE_STATS_PATH.exists()
    data_sources = ("offline", "api") if offline_available else ("api",)
    st.sidebar.radio(
        "Data source",
        options=data_sources,
        format_func=lambda source: "Offline snapshot" if source == "offline" else "TfL API (live)",
        index=0,
        key="data_source",
        help=(
            "Offline is the downloaded route snapshot. API mode fetches the nearby routes live."
            if offline_available
            else "An offline snapshot is not available yet; the app is using TfL's live API."
        ),
    )
    if st.session_state["data_source"] == "api":
        st.sidebar.text_input(
            "Live API routes",
            value="1 2 3 4 5",
            key="api_route_input",
            help="Enter route numbers separated by spaces, for example: 87 77 88",
        )
    navigation = st.navigation(
        [
            st.Page("pages/home.py", title="Home", icon="🏠", default=True),
            st.Page("pages/route_map.py", title="Show", icon="🗺️"),
            st.Page("pages/explore.py", title="Explore", icon="📍"),
            st.Page("pages/go.py", title="Go", icon="🚌"),
            st.Page("pages/statistics.py", title="Statistics", icon="📊"),
            st.Page("pages/spacing.py", title="Spacing", icon="📈"),
        ]
    )
    navigation.run()


if __name__ == "__main__":
    main()

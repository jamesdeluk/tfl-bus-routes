"""A short introduction to the TfL bus-route explorer."""

import streamlit as st


def main() -> None:
    """Show the app's starting page before the map tools."""
    st.title("TfL bus routes")
    st.write("Explore London bus routes and their published stops.")

    st.header("What would you like to do?")
    st.subheader("Show")
    st.write("Enter one or more route numbers to see their routes and stops on the map.")

    st.subheader("Explore")
    st.write("Place a pin to find routes with a stop within a chosen distance.")

    st.subheader("Go")
    st.write("Place two pins to compare routes available at both locations.")

    st.caption("Statistics and Spacing provide additional route-level analysis.")


main()

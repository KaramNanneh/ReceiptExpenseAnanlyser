import pandas as pd
import plotly.express as px
import streamlit as st

from utils import (
    CATEGORIES,
    extract_text_from_receipt,
    load_image,
    parse_receipt_data,
    preprocess_image,
)

st.set_page_config(page_title="Receipt OCR & Expense Analyser", page_icon="🧾", layout="wide")

st.title("Receipt OCR & Expense Analyser")
st.markdown("Upload a photo of your receipt to extract items, categorise expenses, and view interactive insights.")

st.sidebar.header("Upload Options")
uploaded_file = st.sidebar.file_uploader("Choose a receipt image...", type=["jpg", "jpeg", "png"])

CATEGORY_OPTIONS = list(CATEGORIES.keys()) + ["Miscellaneous"]


@st.cache_data(show_spinner=False)
def process(file_bytes):
    import io

    image = load_image(io.BytesIO(file_bytes))
    text = extract_text_from_receipt(image)
    df, receipt_total = parse_receipt_data(text)
    return image, text, df, receipt_total


if uploaded_file is not None:
    with st.spinner("Processing receipt with Tesseract OCR & OpenCV..."):
        image, extracted_text, df_expenses, receipt_total = process(uploaded_file.getvalue())

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Uploaded Receipt")
        st.image(image, caption="Receipt Image", width="stretch")

    with col2:
        st.subheader("Extraction & Analysis")

        if df_expenses.empty:
            st.warning(
                "No line items could be read from this receipt. Check the OCR debug panel below "
                "to see what Tesseract produced, or add items manually in the table."
            )
        else:
            st.success(f"Extracted {len(df_expenses)} items.")

        st.markdown("### Itemised Breakdown")
        edited_df = st.data_editor(
            df_expenses,
            num_rows="dynamic",
            width="stretch",
            column_config={
                "Category": st.column_config.SelectboxColumn("Category", options=CATEGORY_OPTIONS, required=True),
                "Cost (£)": st.column_config.NumberColumn("Cost (£)", format="%.2f", min_value=0.0),
            },
        )

        edited_df = edited_df.copy()
        edited_df["Cost (£)"] = pd.to_numeric(edited_df["Cost (£)"], errors="coerce").fillna(0.0)
        edited_df["Category"] = edited_df["Category"].fillna("Miscellaneous")

        total_spent = edited_df["Cost (£)"].sum()
        st.metric(label="Total Analysed Expense", value=f"£{total_spent:.2f}")

        if receipt_total is not None:
            if abs(receipt_total - total_spent) < 0.01:
                st.success(f"Matches the receipt total (£{receipt_total:.2f}).")
            else:
                st.warning(
                    f"Receipt says £{receipt_total:.2f} but items add up to £{total_spent:.2f}. "
                    f"A line was probably missed or misread - check the table."
                )

    with st.expander("OCR debug: what the computer actually saw"):
        dbg1, dbg2 = st.columns(2)
        with dbg1:
            st.caption("Preprocessed image")
            st.image(preprocess_image(image), width="stretch")
        with dbg2:
            st.caption("Raw OCR text")
            st.code(extracted_text or "(nothing detected)")

    st.markdown("---")
    st.subheader("Spending Insights Dashboard")

    if not edited_df.empty and total_spent > 0:
        category_df = edited_df.groupby("Category")["Cost (£)"].sum().reset_index()

        col_chart1, col_chart2 = st.columns(2)

        with col_chart1:
            fig_pie = px.pie(category_df, names="Category", values="Cost (£)", title="Expenses by Category", hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_chart2:
            fig_bar = px.bar(category_df, x="Category", y="Cost (£)", color="Category",
                               title="Cost Distribution per Category")
            st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.warning("No expense items found to display charts.")
else:
    st.info("Please upload a receipt image from the sidebar to begin.")
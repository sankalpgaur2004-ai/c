import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from typing import Dict, Any, Optional
import logging
import json
import re

logger = logging.getLogger(__name__)

# Project color palette - matching frontend theme
CHART_COLORS = [
    '#FFB162',  # Burning Flame - vivid warm orange
    '#4A6B8A',  # Blue - deep blue
    '#A35139',  # Danger/Rust - warm rust
    '#2C3B4D',  # Navy - deep navy
    '#F5A233',  # Amber - golden amber
    '#7FA3C0',  # Blue Mid - soft blue
    '#FF8C42',  # Deep Flame - richer orange
    '#3A7D52',  # Success Green - forest green
    '#C97E1A',  # Dark Amber - burnt amber
    '#5A8AA6',  # Steel Blue - mid tone blue
]

class VisualizationEngine:
    """Creates appropriate visualizations based on query results"""
    
    def create_visualization(self, data: pd.DataFrame, question: str) -> Optional[Dict[str, Any]]:
        """Create appropriate visualization based on data and question context"""
        
        if data.empty:
            return None
        
        try:
            question_lower = question.lower()
            
            # Single value/scalar result (1 row, 1 column) - create a metric card
            if len(data) == 1 and len(data.columns) == 1:
                return self._create_metric_card(data, question)
            
            # Single value but multiple columns - could still visualize as card with breakdown
            if len(data) == 1:
                return self._create_single_row_viz(data, question)
            
            # Check for time-series data first
            date_cols = self._find_date_columns(data)
            sales_cols = self._find_sales_columns(data)
            
            # If we have both date columns and sales columns, it's likely a trend query
            if date_cols and sales_cols and (
                any(keyword in question_lower for keyword in ['trend', 'over time', 'month', 'quarter', 'year', 'period', 'declining']) or
                self._has_time_dimension(data)
            ):
                logger.info(f"Creating trend chart for question: {question}")
                return self._create_trend_chart(data)
            
            # Detect counting/aggregation queries - use bar charts
            elif any(keyword in question_lower for keyword in ['how many', 'count', 'number of', 'total', 'sum']):
                return self._create_count_chart(data)
            
            # Average/comparison queries - use appropriate visualization
            elif any(keyword in question_lower for keyword in ['average', 'mean', 'per', 'ratio']):
                return self._create_comparison_chart(data)
            
            # Market share charts
            elif 'market share' in question_lower or ('share' in question_lower and any(keyword in question_lower for keyword in ['region', 'territory', 'market'])):
                return self._create_market_share_chart(data)
                
            # Sales volume charts
            elif any(keyword in question_lower for keyword in ['sales', 'revenue', 'amount', 'driving', 'highest']):
                return self._create_sales_chart(data)
                
            # Geographic charts
            elif any(keyword in question_lower for keyword in ['region', 'territory', 'geographic']):
                return self._create_geographic_chart(data)
                
            else:
                return self._create_default_chart(data)
                
        except Exception as e:
            logger.error(f"Visualization creation failed: {e}")
            return self._create_default_chart(data)
    
    def _create_sales_chart(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Create sales-focused visualization"""
        try:
            # Find numerical columns for values and categorical columns for grouping
            numeric_cols = data.select_dtypes(include=['int64', 'float64']).columns.tolist()
            categorical_cols = data.select_dtypes(include=['object']).columns.tolist()
            date_cols = [col for col in data.columns if 'date' in col.lower()]
            
            if 'sale_amount' in numeric_cols or 'amount' in numeric_cols:
                value_col = 'sale_amount' if 'sale_amount' in numeric_cols else 'amount'
            elif len(numeric_cols) > 0:
                # Use the first numeric column if sale_amount doesn't exist
                value_col = numeric_cols[0]
            else:
                return self._create_default_chart(data)
            
            # Determine what to group by
            if 'brand_name' in categorical_cols:
                group_col = 'brand_name'
            elif 'product_id' in categorical_cols or 'product' in categorical_cols:
                group_col = 'product_id' if 'product_id' in categorical_cols else 'product'
            elif len(categorical_cols) > 0:
                # Use the first categorical column
                group_col = categorical_cols[0]
            else:
                return self._create_default_chart(data)
            
            # Choose the chart type based on the data shape
            if len(data) <= 10:
                # Bar chart for small datasets - use discrete colors
                fig = go.Figure(data=[
                    go.Bar(
                        x=data[group_col],
                        y=data[value_col],
                        marker=dict(
                            color=[CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(data))],
                            line=dict(color='#FFFFFF', width=1)
                        ),
                        text=data[value_col],
                        texttemplate='%{text:,.0f}',
                        textposition='outside'
                    )
                ])
                
                fig.update_layout(
                    title=f"{value_col.replace('_', ' ').title()} by {group_col.replace('_', ' ').title()}",
                    xaxis_title=group_col.replace('_', ' ').title(),
                    yaxis_title=value_col.replace('_', ' ').title(),
                    height=450,
                    font=dict(family="DM Sans, sans-serif"),
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(255,255,255,1)',
                    margin=dict(l=60, r=40, t=80, b=100),
                    xaxis_tickangle=-45,
                    showlegend=False
                )
                
                chart_type = "bar"
            else:
                # Create a grouped bar chart for datasets with many categories
                top_groups = data.groupby(group_col)[value_col].sum().nlargest(10).index.tolist()
                filtered_data = data[data[group_col].isin(top_groups)]
                
                fig = go.Figure(data=[
                    go.Bar(
                        x=filtered_data[group_col],
                        y=filtered_data[value_col],
                        marker=dict(
                            color=[CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(filtered_data))],
                            line=dict(color='#FFFFFF', width=1)
                        ),
                        text=filtered_data[value_col],
                        texttemplate='%{text:,.0f}',
                        textposition='outside'
                    )
                ])
                
                fig.update_layout(
                    title=f"Top 10 {group_col.replace('_', ' ').title()} by {value_col.replace('_', ' ').title()}",
                    xaxis_title=group_col.replace('_', ' ').title(),
                    yaxis_title=value_col.replace('_', ' ').title(),
                    height=450,
                    font=dict(family="DM Sans, sans-serif"),
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(255,255,255,1)',
                    margin=dict(l=60, r=40, t=80, b=100),
                    xaxis_tickangle=-45,
                    showlegend=False
                )
                
                chart_type = "bar"
            
            # Convert to JSON-serializable format
            chart_json = {
                "type": chart_type,
                "data": json.loads(fig.to_json())
            }
            return chart_json
            
        except Exception as e:
            logger.error(f"Sales chart creation failed: {e}")
            return self._create_default_chart(data)
    
    def _create_market_share_chart(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Create market share visualization"""
        try:
            # Find columns likely to contain brand or company information
            brand_cols = [col for col in data.columns if any(keyword in col.lower() 
                                                           for keyword in ['brand', 'company', 'product'])]
            
            # Find columns likely to contain value or amount information
            value_cols = [col for col in data.columns if any(keyword in col.lower() 
                                                           for keyword in ['amount', 'value', 'sales', 'quantity', 'share'])]
            
            if not brand_cols or not value_cols:
                return self._create_default_chart(data)
            
            brand_col = brand_cols[0]
            value_col = value_cols[0]
            
            # Create a pie chart for market share with project colors
            fig = go.Figure(data=[
                go.Pie(
                    labels=data[brand_col],
                    values=data[value_col],
                    marker=dict(
                        colors=CHART_COLORS,
                        line=dict(color='#FFFFFF', width=2)
                    ),
                    textinfo='label+percent',
                    textposition='auto',
                    hovertemplate='<b>%{label}</b><br>%{value:,.0f}<br>%{percent}<extra></extra>'
                )
            ])
            
            fig.update_layout(
                title=f"Market Share by {brand_col.replace('_', ' ').title()}",
                height=450,
                font=dict(family="DM Sans, sans-serif"),
                paper_bgcolor='rgba(0,0,0,0)',
                margin=dict(l=40, r=40, t=80, b=40),
                showlegend=True,
                legend=dict(
                    orientation="v",
                    yanchor="middle",
                    y=0.5,
                    xanchor="left",
                    x=1.05
                )
            )
            
            # Convert to JSON-serializable format
            chart_json = {
                "type": "pie",
                "data": json.loads(fig.to_json())
            }
            return chart_json
            
        except Exception as e:
            logger.error(f"Market share chart creation failed: {e}")
            return self._create_default_chart(data)
    
    def _create_trend_chart(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Create time-based trend visualization"""
        try:
            logger.info("Creating trend chart with data columns: %s", data.columns.tolist())
            
            # Find date/time columns
            date_cols = self._find_date_columns(data)
            
            # Find value columns
            value_cols = self._find_sales_columns(data)
            
            # If no obvious date/value columns, try to infer them
            if not date_cols:
                # Check for "MONTH", "QUARTER", "YEAR" columns
                month_cols = [col for col in data.columns if col.upper() in ["MONTH", "MONTH YEAR", "YEAR MONTH"]]
                if month_cols:
                    date_cols = month_cols
                else:
                    # Check for columns that seem to have sequential data (month numbers, etc.)
                    for col in data.columns:
                        if pd.api.types.is_numeric_dtype(data[col]) and data[col].nunique() <= 12:
                            # If values look like months (1-12), it could be a month column
                            if all(1 <= val <= 12 for val in data[col].dropna()):
                                date_cols = [col]
                                break
            
            if not date_cols or not value_cols:
                logger.warning("Couldn't find appropriate date/value columns for trend chart")
                return self._create_default_chart(data)
            
            date_col = date_cols[0]
            value_col = value_cols[0]
            
            logger.info(f"Selected {date_col} for x-axis and {value_col} for y-axis")
            
            # Prepare data - ensure dates are properly sorted
            chart_data = data.copy()
            
            # Handle special date columns
            if 'MONTH YEAR' in chart_data.columns:
                # Ensure proper sorting for "MONTH YEAR" format
                chart_data = chart_data.sort_values('MONTH YEAR')
                date_col = 'MONTH YEAR'
            elif 'YEAR MONTH' in chart_data.columns:
                chart_data = chart_data.sort_values('YEAR MONTH')
                date_col = 'YEAR MONTH'
            elif date_col in chart_data.columns:
                # Try to convert to proper datetime if it's a date string
                try:
                    if pd.api.types.is_object_dtype(chart_data[date_col]):
                        # Check for YYYY-MM format
                        if all(re.match(r'^\d{4}-\d{2}$', str(val)) for val in chart_data[date_col].dropna().head(3)):
                            # Sort by this column directly
                            chart_data = chart_data.sort_values(date_col)
                except Exception as e:
                    logger.warning(f"Error handling date column: {e}")
            
            # Check for a category column to group by
            category_cols = [col for col in chart_data.columns if col not in [date_col, value_col] and 
                           chart_data[col].dtype == 'object' and chart_data[col].nunique() <= 10]
            
            if category_cols:
                # Create a line chart with categories
                category_col = category_cols[0]
                
                fig = px.line(chart_data, x=date_col, y=value_col, color=category_col, markers=True,
                             title=f"{value_col.replace('_', ' ').title()} Trend by {category_col.replace('_', ' ').title()}",
                             labels={date_col: date_col.replace('_', ' ').title(),
                                    value_col: value_col.replace('_', ' ').title(),
                                    category_col: category_col.replace('_', ' ').title()})
                                    
                # Add line of best fit for each category
                for category in chart_data[category_col].unique():
                    cat_data = chart_data[chart_data[category_col] == category]
                    # Only add trend line if enough points
                    if len(cat_data) >= 3:
                        try:
                            fig.add_trace(go.Scatter(
                                x=cat_data[date_col],
                                y=cat_data[value_col],
                                mode='lines',
                                line=dict(dash='dot', width=1),
                                showlegend=False,
                                opacity=0.5,
                                name=f"{category} Trend"
                            ))
                        except Exception as trend_error:
                            logger.warning(f"Error adding trend line: {trend_error}")
            else:
                # Create a line chart with markers
                fig = px.line(chart_data, x=date_col, y=value_col, markers=True,
                             title=f"{value_col.replace('_', ' ').title()} Trend Over Time",
                             labels={date_col: date_col.replace('_', ' ').title(),
                                    value_col: value_col.replace('_', ' ').title()})
                
                # Add trend line for overall data
                if len(chart_data) >= 3:
                    try:
                        # Add a smoothed trend line
                        x_numeric = list(range(len(chart_data)))
                        fig.add_trace(go.Scatter(
                            x=chart_data[date_col],
                            y=chart_data[value_col].rolling(window=min(3, len(chart_data)), min_periods=1).mean(),
                            mode='lines',
                            line=dict(dash='dot', width=2, color='rgba(255,0,0,0.5)'),
                            name="Trend"
                        ))
                    except Exception as trend_error:
                        logger.warning(f"Error adding trend line: {trend_error}")
            
            # Improve layout
            fig.update_layout(
                xaxis_title=date_col.replace('_', ' ').title(),
                yaxis_title=value_col.replace('_', ' ').title(),
                hovermode='closest',
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                )
            )
            
            # Add range slider for time series
            fig.update_layout(
                xaxis=dict(
                    rangeslider=dict(visible=True),
                    type='category'
                )
            )
            
            # Convert to JSON-serializable format
            chart_json = {
                "type": "line",
                "data": json.loads(fig.to_json())
            }
            return chart_json
            
        except Exception as e:
            logger.error(f"Trend chart creation failed: {e}")
            return self._create_default_chart(data)
    
    def _create_geographic_chart(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Create geographic visualization"""
        try:
            # Find region/territory columns
            geo_cols = [col for col in data.columns if any(keyword in col.lower() 
                                                        for keyword in ['region', 'territory', 'area', 'country', 'state'])]
            
            # Find value columns
            value_cols = [col for col in data.columns if any(keyword in col.lower() 
                                                           for keyword in ['amount', 'value', 'sales', 'quantity'])]
            
            if not geo_cols or not value_cols:
                return self._create_default_chart(data)
            
            geo_col = geo_cols[0]
            value_col = value_cols[0]
            
            # Aggregate data by region
            agg_data = data.groupby(geo_col)[value_col].sum().reset_index()
            
            # Create a horizontal bar chart for regions
            fig = px.bar(agg_data, y=geo_col, x=value_col, orientation='h',
                        title=f"{value_col.replace('_', ' ').title()} by {geo_col.replace('_', ' ').title()}",
                        labels={geo_col: geo_col.replace('_', ' ').title(),
                               value_col: value_col.replace('_', ' ').title()})
            
            # Sort by value
            fig.update_layout(yaxis={'categoryorder':'total ascending'})
            
            # Convert to JSON-serializable format
            chart_json = {
                "type": "bar",
                "data": json.loads(fig.to_json())
            }
            return chart_json
            
        except Exception as e:
            logger.error(f"Geographic chart creation failed: {e}")
            return self._create_default_chart(data)
    
    def _create_default_chart(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Create default visualization when specific chart type can't be determined"""
        try:
            # Find numeric and categorical columns
            numeric_cols = data.select_dtypes(include=['int64', 'float64']).columns.tolist()
            categorical_cols = data.select_dtypes(include=['object']).columns.tolist()
            
            if not numeric_cols or not categorical_cols:
                # No plottable columns — skip chart entirely
                return None
            
            # Use the first categorical column and first numeric column
            x_col = categorical_cols[0]
            y_col = numeric_cols[0]
            
            # Create a simple bar chart
            fig = px.bar(data, x=x_col, y=y_col,
                        title=f"{y_col.replace('_', ' ').title()} by {x_col.replace('_', ' ').title()}",
                        labels={x_col: x_col.replace('_', ' ').title(),
                               y_col: y_col.replace('_', ' ').title()})
            
            # Convert to JSON-serializable format
            chart_json = {
                "type": "bar",
                "data": json.loads(fig.to_json())
            }
            return chart_json
            
        except Exception as e:
            logger.error(f"Default chart creation failed: {e}")
            return None
    
    def _find_date_columns(self, data: pd.DataFrame) -> list:
        """Find columns that likely contain date information"""
        # Check column names first
        date_cols = [col for col in data.columns if any(keyword in col.lower() 
                                                     for keyword in ['date', 'time', 'year', 'month', 'quarter'])]
        
        # If no obvious date columns, check for columns with YYYY-MM or YYYY-MM-DD patterns
        if not date_cols:
            for col in data.columns:
                # Skip numeric columns
                if pd.api.types.is_numeric_dtype(data[col]):
                    continue
                
                # Check if column contains date-like strings
                sample_vals = data[col].astype(str).dropna().head(5).tolist()
                if any(re.match(r'^\d{4}-\d{2}', str(val)) for val in sample_vals) or \
                   any(re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}', str(val)) for val in sample_vals):
                    date_cols.append(col)
                    
        # Check for year-month combinations (e.g., "2024-01")
        if not date_cols:
            for col in data.columns:
                if pd.api.types.is_object_dtype(data[col]) and \
                   all(re.match(r'^\d{4}-\d{2}$', str(val)) for val in data[col].dropna().head(5).tolist()):
                    date_cols.append(col)
        
        return date_cols
    
    def _find_sales_columns(self, data: pd.DataFrame) -> list:
        """Find columns that likely contain sales information"""
        # Check for common sales column names
        sales_cols = [col for col in data.columns if any(keyword in col.lower() 
                                                      for keyword in ['sales', 'revenue', 'amount', 'total', 'value'])]
        
        # If no obvious sales columns, look for numeric columns
        if not sales_cols:
            sales_cols = data.select_dtypes(include=['int64', 'float64']).columns.tolist()
            
        return sales_cols
    
    def _has_time_dimension(self, data: pd.DataFrame) -> bool:
        """Check if the data has a clear time dimension"""
        # Check for columns named "MONTH", "QUARTER", "YEAR", etc.
        time_cols = [col for col in data.columns if col.upper() in ["MONTH", "QUARTER", "YEAR"]]
        if time_cols:
            return True
            
        # Check for columns that contain year or date patterns
        for col in data.columns:
            if pd.api.types.is_object_dtype(data[col]):
                sample_vals = data[col].dropna().head(10).astype(str).tolist()
                # Check for year patterns (2020, 2021, etc.)
                if all(re.match(r'^\d{4}$', val) for val in sample_vals if val.isdigit()):
                    return True
                # Check for year-month patterns (2020-01, 2020-02, etc.)
                if all(re.match(r'^\d{4}-\d{2}$', val) for val in sample_vals):
                    return True
                # Check for "YYYY-MM" or "Month YYYY" patterns
                month_patterns = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                if any(month in col for month in month_patterns) or any(any(month in str(val) for month in month_patterns) for val in sample_vals):
                    return True
        
        return False
    
    def _create_metric_card(self, data: pd.DataFrame, question: str) -> Dict[str, Any]:
        """Create a metric card for single scalar values"""
        try:
            col_name = data.columns[0]
            value = data.iloc[0, 0]
            
            # Convert value to numeric, handling different types
            try:
                numeric_value = float(value)
            except (ValueError, TypeError):
                # If conversion fails, try to extract numbers from string
                if isinstance(value, str):
                    import re
                    numbers = re.findall(r'-?\d+\.?\d*', value)
                    numeric_value = float(numbers[0]) if numbers else 0
                else:
                    numeric_value = 0
            
            # Format the value nicely for display
            if abs(numeric_value) >= 1_000_000:
                formatted_value = f"{numeric_value/1_000_000:.2f}M"
            elif abs(numeric_value) >= 1_000:
                formatted_value = f"{numeric_value/1_000:.1f}K"
            else:
                # Show whole number if it's an integer, otherwise 2 decimals
                if numeric_value == int(numeric_value):
                    formatted_value = f"{int(numeric_value):,}"
                else:
                    formatted_value = f"{numeric_value:.2f}"
            
            # Create a simple indicator chart
            fig = go.Figure(go.Indicator(
                mode = "number",
                value = numeric_value,
                title = {"text": col_name.replace('_', ' ').title(), "font": {"size": 18, "color": "#2C3B4D"}},
                number = {"font": {"size": 72, "color": "#1B2632"}, "valueformat": ","},
                domain = {'x': [0, 1], 'y': [0, 1]}
            ))
            
            fig.update_layout(
                height=280,
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                margin=dict(l=40, r=40, t=80, b=40),
                font=dict(family="DM Sans, sans-serif")
            )
            
            return {
                "type": "indicator",
                "data": json.loads(fig.to_json())
            }
        except Exception as e:
            logger.error(f"Metric card creation failed: {e}")
            return None
    
    def _create_single_row_viz(self, data: pd.DataFrame, question: str) -> Dict[str, Any]:
        """Create visualization for single row with multiple columns"""
        try:
            # If all columns are numeric, show as bar chart
            numeric_cols = data.select_dtypes(include=['int64', 'float64']).columns.tolist()
            
            if len(numeric_cols) >= len(data.columns) - 1:
                # Create a simple bar chart
                fig = go.Figure(go.Bar(
                    x=data.columns.tolist(),
                    y=data.iloc[0].tolist(),
                    marker_color='steelblue'
                ))
                
                fig.update_layout(
                    title="Metric Breakdown",
                    xaxis_title="Metrics",
                    yaxis_title="Values",
                    height=400,
                    showlegend=False
                )
                
                return {
                    "type": "bar",
                    "data": json.loads(fig.to_json())
                }
            else:
                # Fall back to metric card for the first numeric column
                return self._create_metric_card(data[[numeric_cols[0]]], question) if numeric_cols else None
        except Exception as e:
            logger.error(f"Single row viz creation failed: {e}")
            return None
    
    def _create_count_chart(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Create visualization for counting queries"""
        try:
            # Find categorical and numeric columns
            numeric_cols = data.select_dtypes(include=['int64', 'float64']).columns.tolist()
            categorical_cols = data.select_dtypes(include=['object']).columns.tolist()
            
            if not numeric_cols:
                return None
            
            value_col = numeric_cols[0]
            
            # If we have a category column, create a bar chart
            if categorical_cols:
                group_col = categorical_cols[0]
                
                # Sort by value descending for better visualization
                sorted_data = data.sort_values(by=value_col, ascending=False).head(15)
                
                fig = go.Figure(data=[
                    go.Bar(
                        x=sorted_data[group_col],
                        y=sorted_data[value_col],
                        marker=dict(
                            color=[CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(sorted_data))],
                            line=dict(color='#FFFFFF', width=1)
                        ),
                        text=sorted_data[value_col],
                        texttemplate='%{text:,.0f}',
                        textposition='outside'
                    )
                ])
                
                fig.update_layout(
                    title=f"{value_col.replace('_', ' ').title()} by {group_col.replace('_', ' ').title()}",
                    xaxis_title=group_col.replace('_', ' ').title(),
                    yaxis_title=value_col.replace('_', ' ').title(),
                    xaxis_tickangle=-45,
                    height=450,
                    showlegend=False,
                    font=dict(family="DM Sans, sans-serif"),
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(255,255,255,1)',
                    margin=dict(l=60, r=40, t=80, b=100)
                )
                
                return {
                    "type": "bar",
                    "data": json.loads(fig.to_json())
                }
            else:
                # Single value - create metric card
                return self._create_metric_card(data[[value_col]], "")
                
        except Exception as e:
            logger.error(f"Count chart creation failed: {e}")
            return None
    
    def _create_comparison_chart(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Create visualization for comparison/average queries"""
        try:
            numeric_cols = data.select_dtypes(include=['int64', 'float64']).columns.tolist()
            categorical_cols = data.select_dtypes(include=['object']).columns.tolist()
            
            if not numeric_cols:
                return None
            
            value_col = numeric_cols[0]
            
            # Single value metric - show as card
            if len(data) == 1 and not categorical_cols:
                return self._create_metric_card(data[[value_col]], "")
            
            # Multiple values with categories - show as horizontal bar for better comparison
            if categorical_cols and len(data) > 1:
                group_col = categorical_cols[0]
                sorted_data = data.sort_values(by=value_col, ascending=True).tail(15)
                
                fig = go.Figure(data=[
                    go.Bar(
                        y=sorted_data[group_col],
                        x=sorted_data[value_col],
                        orientation='h',
                        marker=dict(
                            color=[CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(sorted_data))],
                            line=dict(color='#FFFFFF', width=1)
                        ),
                        text=sorted_data[value_col],
                        texttemplate='%{text:,.1f}',
                        textposition='outside'
                    )
                ])
                
                fig.update_layout(
                    title=f"{value_col.replace('_', ' ').title()} Comparison",
                    xaxis_title=value_col.replace('_', ' ').title(),
                    yaxis_title=group_col.replace('_', ' ').title(),
                    height=max(400, len(sorted_data) * 35),
                    font=dict(family="DM Sans, sans-serif"),
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(255,255,255,1)',
                    margin=dict(l=150, r=80, t=80, b=60),
                    showlegend=False
                )
                
                return {
                    "type": "bar",
                    "data": json.loads(fig.to_json())
                }
            
            return self._create_default_chart(data)
            
        except Exception as e:
            logger.error(f"Comparison chart creation failed: {e}")
            return None
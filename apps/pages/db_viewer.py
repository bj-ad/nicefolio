"""
Database Viewer Page - Debug Tool
Displays all database tables and their data for debugging and verification.
"""

import os
from decimal import Decimal
from datetime import datetime, date
from pathlib import Path
from nicegui import ui, app
from sqlalchemy import create_engine, inspect, text
from dotenv import load_dotenv
from utils.datetime_utils import now_utc
import pandas as pd
import io

from apps.core.layout import page_layout

# Load environment variables
load_dotenv()

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

# Log file path
LOG_DIR = Path(__file__).parent.parent.parent / 'logs'
LOG_FILE = LOG_DIR / 'app.log'


def load_schema():
    """Retrieve all tables and their columns from the database."""
    inspector = inspect(engine)
    schema = {}
    for table_name in inspector.get_table_names():
        columns = inspector.get_columns(table_name)
        schema[table_name] = [column['name'] for column in columns]
    return schema


def serialize_value(value):
    """Convert non-JSON-serializable values to strings."""
    if value is None or pd.isna(value):
        return None
    elif isinstance(value, (datetime, date)):
        return value.isoformat()
    elif isinstance(value, Decimal):
        return float(value)
    elif isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return str(value)
    elif isinstance(value, (int, float, str, bool)):
        return value
    elif hasattr(value, '__str__'):
        return str(value)
    else:
        return value


def load_table_data(table_name):
    """Load all data from a given table into a Pandas DataFrame, sorted by latest data first."""
    with engine.connect() as connection:
        result = connection.execute(text(f"SELECT * FROM {table_name}"))
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
        
        # Sort by ID descending (latest data first) if 'id' column exists
        if 'id' in df.columns:
            df = df.sort_values(by='id', ascending=False)
        # Otherwise, try to sort by common timestamp columns
        elif 'created_at' in df.columns:
            df = df.sort_values(by='created_at', ascending=False)
        elif 'updated_at' in df.columns:
            df = df.sort_values(by='updated_at', ascending=False)
        elif 'date' in df.columns:
            df = df.sort_values(by='date', ascending=False)
        
        # Convert datetime/timestamp columns to strings for JSON serialization
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].astype(str)
            # Handle SQLAlchemy Timestamp, Decimal, and other non-serializable types
            elif df[col].dtype == 'object':
                df[col] = df[col].apply(serialize_value)
        
    return df


def load_log_files():
    """Get list of available log files."""
    if not LOG_DIR.exists():
        return []
    
    log_files = []
    # Include app.log* and restic_backup.log*
    for pattern in ['app.log*', 'restic_backup.log*']:
        log_files.extend(LOG_DIR.glob(pattern))
    
    log_files = sorted(
        log_files,
        key=lambda x: x.stat().st_mtime,
        reverse=True  # Most recent first
    )
    return log_files


def read_log_file(log_file_path: Path, max_lines: int = 1000):
    """Read the last N lines from a log file."""
    try:
        if not log_file_path.exists():
            return "Log file not found."
        
        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        # Return last max_lines
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        
        return ''.join(lines)
    except Exception as e:
        return f"Error reading log file: {str(e)}"


def search_logs(log_file_path: Path, search_term: str, max_lines: int = 500):
    """Search for a term in the log file and return matching lines with context."""
    try:
        if not log_file_path.exists():
            return "Log file not found."
        
        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        matching_lines = []
        for i, line in enumerate(lines):
            if search_term.lower() in line.lower():
                # Add line number and content
                matching_lines.append(f"Line {i+1}: {line}")
                
                if len(matching_lines) >= max_lines:
                    break
        
        if matching_lines:
            return ''.join(matching_lines)
        else:
            return f"No matches found for '{search_term}'"
    except Exception as e:
        return f"Error searching log file: {str(e)}"


def database_viewer_content():
    """Database viewer tab content"""
    ui.label('Database Tables, Columns, and Data').classes('text-h6 q-mb-md')
    
    schema = load_schema()
    
    if schema:
        for table_name, columns in sorted(schema.items()):
            # Create a refreshable table loader for each table
            _create_table_expansion(table_name, columns)
    else:
        ui.label('No tables found in the database.').classes('text-body1 text-warning')


def _create_table_expansion(table_name: str, columns: list):
    """Create an expansion panel for a single table with lazy loading"""
    
    @ui.refreshable
    def table_content():
        """Refreshable content that loads when expansion is opened"""
        storage_key = f'table_loaded_{table_name}'
        
        if not app.storage.user.get(storage_key, False):
            # Show loading spinner
            with ui.column().classes('items-center justify-center py-8'):
                ui.spinner('dots', size='lg').classes('text-blue-500')
                ui.label(f'Loading {table_name} data...').classes('text-gray-500 text-sm mt-2')
            
            # Use active flag to prevent timer from executing on destroyed context
            table_active = {'value': True}
            
            # Defer table data load
            def load_table():
                if not table_active['value']:
                    return
                try:
                    app.storage.user[storage_key] = True
                    table_content.refresh()
                except RuntimeError as e:
                    if 'parent slot' in str(e).lower():
                        logger.debug(f'Table load timer cancelled for {table_name} - user navigated away')
                    else:
                        logger.error(f"Error loading table {table_name}: {e}")
                except Exception as e:
                    logger.error(f"Error loading table {table_name}: {e}")
            
            ui.timer(0.05, load_table, once=True)
            ui.context.client.on_disconnect(lambda: table_active.__setitem__('value', False))
            return
        
        # Load and display actual table data
        try:
            table_data = load_table_data(table_name)
            
            if len(table_data) > 0:
                # Row count and download button
                with ui.row().classes('items-center justify-between w-full q-mb-sm'):
                    ui.label(f'Total rows: {len(table_data)}').classes('text-subtitle2')
                    
                    # Download button
                    def create_download_handler(tn, td):
                        def download():
                            # Convert DataFrame to CSV
                            csv_buffer = io.StringIO()
                            td.to_csv(csv_buffer, index=False)
                            csv_content = csv_buffer.getvalue()
                            
                            # Generate filename with timestamp
                            timestamp = now_utc().strftime('%Y%m%d_%H%M%S')
                            filename = f"{tn}_{timestamp}.csv"
                            
                            # Trigger download
                            ui.download(csv_content.encode('utf-8'), filename)
                            ui.notify(f'Downloaded {filename}', type='positive')
                        
                        return download
                    
                    ui.button(
                        'Download CSV', 
                        icon='download',
                        on_click=create_download_handler(table_name, table_data)
                    ).props('flat dense').classes('text-blue-500')
                
                # Convert DataFrame to format suitable for ui.table
                columns_def = [
                    {'name': col, 'label': col, 'field': col, 'sortable': True, 'align': 'left'}
                    for col in table_data.columns
                ]
                
                # Convert to dict and ensure all values are JSON serializable
                rows = table_data.to_dict('records')
                
                # Final serialization pass to catch any remaining non-serializable types
                rows = [
                    {k: serialize_value(v) for k, v in row.items()}
                    for row in rows
                ]
                
                # Display table with pagination controls in scrollable container
                with ui.element('div').classes('w-full overflow-x-auto border rounded-lg'):
                    table = ui.table(
                        columns=columns_def,
                        rows=rows,
                        row_key='id' if 'id' in table_data.columns else columns[0],
                        pagination={'rowsPerPage': 20, 'sortBy': None, 'descending': False, 'page': 1}
                    ).props('dense flat bordered separator="cell"').classes('w-full').style('max-height: 600px;')
                
                # Add pagination info and controls
                ui.label(f'Sorted by: Latest first (descending)').classes('text-caption text-grey-6 mt-2')
            else:
                ui.label('No data in table').classes('text-body2 text-grey-6 italic')
        
        except Exception as e:
            ui.label(f'Error loading data: {str(e)}').classes('text-negative')
    
    # Create expansion with table content
    with ui.expansion(table_name, icon='table_chart').classes('w-full q-mb-md') as expansion:
        # Column info (always visible)
        ui.label(f'Columns ({len(columns)}):').classes('text-subtitle2 text-weight-bold')
        ui.label(', '.join(columns)).classes('text-body2 text-grey-7 q-mb-md')
        
        # Table data (loaded on demand)
        table_content()
        
        # Reset loading state when expansion is closed
        def on_value_change(e):
            # e.args is the new value from the expansion's model-value event
            if not e.args:  # Expansion closed (False)
                storage_key = f'table_loaded_{table_name}'
                if storage_key in app.storage.user:
                    del app.storage.user[storage_key]
        
        expansion.on('update:model-value', on_value_change)


def log_viewer_content():
    """Log viewer tab content"""
    ui.label('Application Logs').classes('text-h6 q-mb-md')
    
    # Get available log files
    log_files = load_log_files()
    
    if not log_files:
        ui.label('No log files found.').classes('text-body1 text-warning')
        ui.label(f'Expected location: {LOG_DIR}').classes('text-caption text-grey-6')
        return
    
    # Log file selector
    with ui.row().classes('items-center gap-4 mb-4 w-full'):
        log_file_select = ui.select(
            label='Select Log File',
            options={str(f): f.name for f in log_files},
            value=str(log_files[0]) if log_files else None
        ).classes('w-64')
        
        lines_select = ui.select(
            label='Max Lines',
            options=[100, 500, 1000, 2000, 5000],
            value=1000
        ).classes('w-32')
        
        def refresh_logs():
            log_content.refresh()
            ui.notify('Logs refreshed', type='positive')
        
        refresh_btn = ui.button('🔄 Refresh', on_click=refresh_logs)
        
        def download_log():
            selected_file = Path(log_file_select.value) if log_file_select.value else log_files[0]
            try:
                with open(selected_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                ui.download(content.encode('utf-8'), selected_file.name)
                ui.notify(f'Downloaded {selected_file.name}', type='positive')
            except Exception as e:
                ui.notify(f'Download failed: {str(e)}', type='negative')
        
        download_btn = ui.button('⬇️ Download', on_click=download_log)
        
    # Search functionality
    with ui.row().classes('items-center gap-2 mb-4 w-full'):
        search_input = ui.input(label='Search logs', placeholder='Enter search term...').classes('flex-grow')
        search_btn = ui.button('🔍 Search', on_click=lambda: perform_search())
        clear_btn = ui.button('Clear', on_click=lambda: clear_search())
    
    # Container for search results
    search_results_container = ui.column().classes('w-full')
    
    # Log content display
    @ui.refreshable
    def log_content():
        selected_file = Path(log_file_select.value) if log_file_select.value else log_files[0]
        max_lines = lines_select.value
        
        log_text = read_log_file(selected_file, max_lines)
        
        with ui.card().classes('w-full'):
            ui.label(f'📄 {selected_file.name}').classes('text-subtitle2 text-weight-bold')
            ui.label(f'Showing last {max_lines} lines').classes('text-caption text-grey-6 mb-2')
            
            ui.code(log_text).classes('w-full').style('max-height: 600px; overflow-y: auto; font-size: 12px;')
    
    def perform_search():
        selected_file = Path(log_file_select.value) if log_file_select.value else log_files[0]
        search_term = search_input.value
        
        if not search_term:
            ui.notify('Please enter a search term', type='warning')
            return
        
        result_text = search_logs(selected_file, search_term, max_lines=500)
        
        # Clear existing search results
        search_results_container.clear()
        
        # Add new search results
        with search_results_container:
            with ui.card().classes('w-full mt-4'):
                ui.label(f'🔍 Search Results for: "{search_term}"').classes('text-subtitle2 text-weight-bold')
                ui.code(result_text).classes('w-full').style('max-height: 400px; overflow-y: auto; font-size: 12px;')
        
        ui.notify(f'Search completed for "{search_term}"', type='positive')
    
    def clear_search():
        search_input.value = ''
        search_results_container.clear()
        ui.notify('Search cleared', type='info')
    
    # Display log content
    log_content()


@ui.page('/db-viewer')
def db_viewer_page():
    """Database viewer page - browse and inspect database tables"""
    
    # Clear loading states on page load
    if 'db_viewer_loaded' in app.storage.user:
        del app.storage.user['db_viewer_loaded']
    if 'log_viewer_loaded' in app.storage.user:
        del app.storage.user['log_viewer_loaded']
    
    with page_layout('/db-viewer'):
        with ui.tabs().classes('w-full') as tabs:
            log_tab = ui.tab('Logs')
            db_tab = ui.tab('Database')
        
        with ui.tab_panels(tabs, value=log_tab).classes('w-full') as panels:
            # Clear loading states when switching tabs
            def on_tab_change(e):
                if 'db_viewer_loaded' in app.storage.user:
                    del app.storage.user['db_viewer_loaded']
                if 'log_viewer_loaded' in app.storage.user:
                    del app.storage.user['log_viewer_loaded']
            
            panels.on('update:model-value', on_tab_change)
            
            with ui.tab_panel(log_tab):
                _log_viewer_wrapper()
            with ui.tab_panel(db_tab):
                _database_viewer_wrapper()


@ui.refreshable
def _database_viewer_wrapper():
    """Refreshable wrapper for database viewer with loading state"""
    if not app.storage.user.get('db_viewer_loaded', False):
        # Show loading spinner
        with ui.column().classes('items-center justify-center py-16'):
            ui.spinner('dots', size='xl').classes('text-blue-500')
            ui.label('Loading database schema...').classes('text-gray-500 text-sm mt-4')
        
        # Use active flag to prevent timer from executing on destroyed context
        viewer_active = {'value': True}
        
        def load_db_viewer():
            if not viewer_active['value']:
                return
            try:
                app.storage.user['db_viewer_loaded'] = True
                # Check client exists before refresh (user may have navigated away)
                if ui.context.client:
                    _database_viewer_wrapper.refresh()
            except RuntimeError as e:
                if 'parent slot' in str(e).lower():
                    logger.debug('DB viewer timer cancelled - user navigated away')
                else:
                    logger.error(f"Error loading DB viewer: {e}")
            except Exception as e:
                logger.error(f"Error loading DB viewer: {e}")
        
        ui.timer(0.05, load_db_viewer, once=True)
        ui.context.client.on_disconnect(lambda: viewer_active.__setitem__('value', False))
        return
    
    # Render actual content
    database_viewer_content()


@ui.refreshable
def _log_viewer_wrapper():
    """Refreshable wrapper for log viewer with loading state"""
    if not app.storage.user.get('log_viewer_loaded', False):
        # Show loading spinner
        with ui.column().classes('items-center justify-center py-16'):
            ui.spinner('dots', size='xl').classes('text-orange-500')
            ui.label('Loading log files...').classes('text-gray-500 text-sm mt-4')
        
        # Use active flag to prevent timer from executing on destroyed context
        log_viewer_active = {'value': True}
        
        def load_log_viewer():
            if not log_viewer_active['value']:
                return
            try:
                app.storage.user['log_viewer_loaded'] = True
                # Check client exists before refresh (user may have navigated away)
                if ui.context.client:
                    _log_viewer_wrapper.refresh()
            except RuntimeError as e:
                if 'parent slot' in str(e).lower():
                    logger.debug('Log viewer timer cancelled - user navigated away')
                else:
                    logger.error(f"Error loading log viewer: {e}")
            except Exception as e:
                logger.error(f"Error loading log viewer: {e}")
                _log_viewer_wrapper.refresh()
        
        ui.timer(0.05, load_log_viewer, once=True)
        ui.context.client.on_disconnect(lambda: log_viewer_active.__setitem__('value', False))
        return
    
    # Render actual content
    log_viewer_content()

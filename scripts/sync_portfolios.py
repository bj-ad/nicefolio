from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Portfolio
from utils.logging_config import get_logger
from utils.portfolios_loader import get_portfolios_loader
from utils.app_config import get_global_base_currency

logger = get_logger(__name__)

def sync_portfolios_from_config(db: Session):
    """
    Reads portfolios from portfolio_config.yaml and upserts them into the database.
    Uses global currency_base from app_config.yaml.
    """
    logger.info("Starting portfolio sync from config")
    
    global_base_currency = get_global_base_currency()
    logger.info(f"Using global base currency: {global_base_currency}")
    
    portfolios_loader = get_portfolios_loader()
    portfolios_in_config = portfolios_loader.get_portfolios()
    
    config_ids = {p['id'] for p in portfolios_in_config}
    
    # Upsert portfolios from config
    for p_data in portfolios_in_config:
        portfolio_id = p_data['id']
        existing_portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
        
        # Filter out fields that are not in the Portfolio model
        # Portfolio model has: id, name, currency_base, description, status, created_at, last_updated
        # NOTE: currency_base now comes from app_config, not portfolio_config
        portfolio_fields = {
            'id': p_data['id'],
            'name': p_data['name'],
            'currency_base': global_base_currency,  # Use global base currency
            'description': p_data.get('description'),
            'status': p_data.get('status', 'active')  # Default to 'active' if not specified
        }
        
        if existing_portfolio:
            logger.debug(f"Updating portfolio ID {portfolio_id}: {p_data['name']}")
            existing_portfolio.name = portfolio_fields['name']
            existing_portfolio.currency_base = portfolio_fields['currency_base']
            existing_portfolio.description = portfolio_fields['description']
            existing_portfolio.status = portfolio_fields['status']
            existing_portfolio.last_updated = datetime.now(timezone.utc)
        else:
            logger.info(f"Inserting new portfolio ID {portfolio_id}: {p_data['name']}")
            new_portfolio = Portfolio(**portfolio_fields)
            db.add(new_portfolio)
            
    # Optional: Delete portfolios from DB that are no longer in the config
    portfolios_in_db = db.query(Portfolio).all()
    for p_in_db in portfolios_in_db:
        if p_in_db.id not in config_ids:
            logger.warning(f"Deleting portfolio ID {p_in_db.id}: '{p_in_db.name}' as it is no longer in the config file.")
            db.delete(p_in_db)

    db.commit()
    logger.info("Portfolio sync completed successfully.")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        sync_portfolios_from_config(db)
    finally:
        db.close()

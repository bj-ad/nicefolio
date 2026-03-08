from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database import SessionLocal, Base, engine
from models import Account
from utils.logging_config import get_logger
from utils.accounts_loader import get_accounts_loader

logger = get_logger(__name__)

def sync_accounts_from_config(db: Session):
    """
    Reads accounts from accounts_config.yaml and upserts them into the database.
    'Upsert' = Update if exists, Insert if not.
    
    Synced fields:
    - name: Account display name
    - type: Account type (Exchange, Broker, etc.)
    - status: 'active' or 'closed' (default: 'active')
    - currency_native: Account's default currency (optional)
    """
    logger.info("Starting account sync from config")
    
    accounts_loader = get_accounts_loader()
    accounts_in_config = accounts_loader.get_accounts()
    
    config_ids = {acc['id'] for acc in accounts_in_config}
    
    # Upsert accounts from config
    for acc_data in accounts_in_config:
        account_id = acc_data['id']
        existing_account = db.query(Account).filter(Account.id == account_id).one_or_none()
        
        if existing_account:
            # Update existing account
            logger.debug(f"Updating account ID {account_id}: {acc_data['name']}")
            existing_account.name = acc_data['name']
            existing_account.type = acc_data['type']
            # status defaults to 'active' if not specified in config
            existing_account.status = acc_data.get('status', 'active')
            # currency_native is optional - only update if present in config
            if 'currency_native' in acc_data:
                existing_account.currency_native = acc_data['currency_native']
            existing_account.last_updated = datetime.now(timezone.utc)
        else:
            # Insert new account
            logger.info(f"Inserting new account ID {account_id}: {acc_data['name']}")
            new_account = Account(
                id=acc_data['id'],
                name=acc_data['name'],
                type=acc_data['type'],
                status=acc_data.get('status', 'active'),
                currency_native=acc_data.get('currency_native'),
                last_updated=datetime.now(timezone.utc)
            )
            db.add(new_account)
            
    # Optional: Delete accounts from DB that are no longer in the config
    accounts_in_db = db.query(Account).all()
    for acc_in_db in accounts_in_db:
        if acc_in_db.id not in config_ids:
            logger.warning(f"Deleting account ID {acc_in_db.id}: '{acc_in_db.name}' as it is no longer in the config file.")
            db.delete(acc_in_db)

    db.commit()
    logger.info("Account sync completed successfully.")


if __name__ == "__main__":
    # This makes the script runnable from the command line
    db = SessionLocal()
    try:
        # You might need to create tables if they don't exist
        # Base.metadata.create_all(bind=engine) 
        sync_accounts_from_config(db)
    finally:
        db.close()

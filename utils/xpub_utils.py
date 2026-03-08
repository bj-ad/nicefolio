"""
Bitcoin xpub (Extended Public Key) utilities for address derivation and transaction tracking.

This module provides functionality to:
1. Derive Bitcoin addresses from xpub keys using proper BIP32 derivation
2. Support different address types (Legacy, SegWit, Native SegWit)
3. Implement BIP44/49/84 derivation paths
4. Handle gap limit logic for address discovery

Implementation uses bip32 library which requires C compiler (gcc, g++, cmake).
Docker compose files install build-essential before pip install to compile the library.
"""

import base58
import hashlib
from typing import List, Dict, Optional
from utils.logging_config import get_logger

logger = get_logger(__name__)

# Try to import bip32 for proper BIP32 derivation
try:
    from bip32 import BIP32
    from bech32 import bech32_encode, convertbits
    BIP32_AVAILABLE = True
    logger.info("bip32 library loaded - using proper BIP32 derivation")
except ImportError as e:
    BIP32_AVAILABLE = False
    logger.warning(f"bip32 library not available - address derivation will fail: {e}")


class XpubProcessor:
    """Handles Bitcoin xpub processing and address derivation."""
    
    def __init__(self, xpub: str, derivation_path: Optional[str] = None, address_type: Optional[str] = 'native_segwit'):
        """
        Initialize xpub processor.
        
        Args:
            xpub: Extended public key (xpub/ypub/zpub)
            derivation_path: BIP44 derivation path (e.g., "m/84'/0'/0'")
            address_type: Address type - 'legacy', 'segwit', or 'native_segwit'
        """
        if not BIP32_AVAILABLE:
            raise ImportError("bip32 library is required for xpub derivation. Install with: pip install bip32")
        
        self.original_xpub = xpub
        self.xpub = self._convert_to_xpub(xpub)  # Convert zpub/ypub to xpub for bip32 library
        self.derivation_path = derivation_path or self._detect_derivation_path(self.original_xpub)
        self.address_type = address_type or self._detect_address_type(self.original_xpub)
        
        # Initialize BIP32 object
        try:
            self.bip32 = BIP32.from_xpub(self.xpub)
        except Exception as e:
            logger.error(f"Failed to initialize BIP32 from xpub: {e}")
            raise
    
    def _convert_to_xpub(self, extended_key: str) -> str:
        """
        Convert zpub/ypub to xpub format for bip32 library compatibility.
        
        The bip32 library only accepts xpub/tpub formats, but we need to support:
        - xpub (Legacy P2PKH) - version bytes: 0x0488b21e
        - ypub (Nested SegWit P2SH-P2WPKH) - version bytes: 0x049d7cb2  
        - zpub (Native SegWit P2WPKH) - version bytes: 0x04b24746
        
        We convert all to xpub format, then derive addresses in the correct format.
        """
        if extended_key.startswith('xpub') or extended_key.startswith('tpub'):
            return extended_key  # Already in correct format
        
        try:
            # Decode the extended key
            decoded = base58.b58decode_check(extended_key)
            
            # Replace version bytes with xpub version (0x0488b21e for mainnet)
            if extended_key.startswith('vpub') or extended_key.startswith('upub'):
                # Testnet versions - convert to tpub (0x043587cf)
                xpub_bytes = bytes.fromhex('043587cf') + decoded[4:]
            else:
                # Mainnet versions (ypub, zpub) - convert to xpub (0x0488b21e)
                xpub_bytes = bytes.fromhex('0488b21e') + decoded[4:]
            
            # Encode back to base58
            converted = base58.b58encode_check(xpub_bytes).decode('ascii')
            logger.debug(f"Converted {extended_key[:4]}... to {converted[:4]}... for bip32 library")
            return converted
            
        except Exception as e:
            logger.error(f"Failed to convert {extended_key[:4]}... to xpub: {e}")
            raise
        
    def _detect_derivation_path(self, xpub: str) -> str:
        """Detect derivation path based on xpub prefix."""
        if xpub.startswith('xpub'):
            return "m/44'/0'/0'"  # Legacy
        elif xpub.startswith('ypub'):
            return "m/49'/0'/0'"  # SegWit
        elif xpub.startswith('zpub'):
            return "m/84'/0'/0'"  # Native SegWit
        else:
            return "m/84'/0'/0'"  # Default to Native SegWit
    
    def _detect_address_type(self, xpub: str) -> str:
        """Detect address type based on xpub prefix."""
        if xpub.startswith('xpub'):
            return 'legacy'
        elif xpub.startswith('ypub'):
            return 'segwit'
        elif xpub.startswith('zpub'):
            return 'native_segwit'
        else:
            return 'native_segwit'  # Default
    
    def derive_address(self, change: int, index: int) -> Optional[str]:
        """
        Derive a Bitcoin address from xpub using proper BIP32 derivation.
        
        Args:
            change: Change chain (0 for receiving, 1 for change addresses)
            index: Address index
            
        Returns:
            Bitcoin address string or None on error
        """
        try:
            # Derive child key at change/index path
            child_key = self.bip32.get_pubkey_from_path([change, index])
            
            # Convert to address based on type
            if self.address_type == 'legacy':
                address = self._pubkey_to_p2pkh_address(child_key)
            elif self.address_type == 'segwit':
                address = self._pubkey_to_p2sh_p2wpkh_address(child_key)
            else:  # native_segwit
                address = self._pubkey_to_p2wpkh_address(child_key)
            
            logger.debug(f"Derived address {address} at {change}/{index}")
            return address
            
        except Exception as e:
            logger.error(f"Error deriving address for {self.xpub[:20]}... at {change}/{index}: {e}")
            return None
    
    def _pubkey_to_p2pkh_address(self, pubkey: bytes) -> str:
        """Convert public key to P2PKH (Legacy) address."""
        # SHA256 then RIPEMD160
        sha256_hash = hashlib.sha256(pubkey).digest()
        ripemd160 = hashlib.new('ripemd160', sha256_hash).digest()
        
        # Add version byte (0x00 for mainnet)
        versioned = b'\x00' + ripemd160
        
        # Double SHA256 for checksum
        checksum = hashlib.sha256(hashlib.sha256(versioned).digest()).digest()[:4]
        
        # Base58 encode
        address = base58.b58encode(versioned + checksum).decode('ascii')
        return address
    
    def _pubkey_to_p2wpkh_address(self, pubkey: bytes) -> str:
        """Convert public key to P2WPKH (Native SegWit / Bech32) address."""
        # SHA256 then RIPEMD160
        sha256_hash = hashlib.sha256(pubkey).digest()
        ripemd160 = hashlib.new('ripemd160', sha256_hash).digest()
        
        # Convert to 5-bit array for bech32
        converted = convertbits(ripemd160, 8, 5)
        
        # Bech32 encode with witness version 0
        address = bech32_encode('bc', [0] + converted)
        return address
    
    def _pubkey_to_p2sh_p2wpkh_address(self, pubkey: bytes) -> str:
        """Convert public key to P2SH-P2WPKH (Nested SegWit) address."""
        # SHA256 then RIPEMD160 of pubkey
        sha256_hash = hashlib.sha256(pubkey).digest()
        ripemd160 = hashlib.new('ripemd160', sha256_hash).digest()
        
        # Create witness program (OP_0 + 20-byte pubkey hash)
        witness_program = b'\x00\x14' + ripemd160
        
        # Hash160 of witness program
        script_hash = hashlib.new('ripemd160', hashlib.sha256(witness_program).digest()).digest()
        
        # Add version byte (0x05 for P2SH mainnet)
        versioned = b'\x05' + script_hash
        
        # Double SHA256 for checksum
        checksum = hashlib.sha256(hashlib.sha256(versioned).digest()).digest()[:4]
        
        # Base58 encode
        address = base58.b58encode(versioned + checksum).decode('ascii')
        return address
    
    def discover_used_addresses(self, gap_limit: int = 20) -> List[Dict]:
        """
        Discover all used addresses up to the gap limit.
        
        Args:
            gap_limit: Maximum number of consecutive unused addresses to check
            
        Returns:
            List of dictionaries with address info and usage status
        """
        used_addresses = []
        
        # Check both receiving (0) and change (1) chains
        for change_chain in [0, 1]:
            consecutive_unused = 0
            index = 0
            
            while consecutive_unused < gap_limit:
                address = self.derive_address(change_chain, index)
                if not address:
                    index += 1
                    consecutive_unused += 1
                    continue
                
                # Check if address has been used (has transactions)
                has_transactions = self._check_address_usage(address)
                
                address_info = {
                    'address': address,
                    'change': change_chain,
                    'index': index,
                    'has_transactions': has_transactions,
                    'derivation_path': f"{self.derivation_path}/{change_chain}/{index}"
                }
                
                if has_transactions:
                    used_addresses.append(address_info)
                    consecutive_unused = 0  # Reset gap counter
                else:
                    consecutive_unused += 1
                
                index += 1
        
        logger.info(f"Discovered {len(used_addresses)} used addresses for xpub {self.xpub[:20]}...")
        return used_addresses
    
    def _check_address_usage(self, address: str) -> bool:
        """
        Check if an address has been used (has transactions).
        Uses Blockstream API for verification.
        """
        from utils.api_client import make_api_call
        
        try:
            url = f"https://blockstream.info/api/address/{address}"
            response = make_api_call(url, retries=2, delay=1, timeout=10)
            
            if response:
                tx_count = response.get('chain_stats', {}).get('tx_count', 0)
                return tx_count > 0
            
            return False
            
        except Exception as e:
            logger.warning(f"Could not check usage for address {address}: {e}")
            return False


def get_all_addresses_from_xpub(xpub: str, gap_limit: int = 20, 
                               address_type: Optional[str] = None) -> List[str]:
    """
    Get all used addresses derived from an xpub.
    
    Args:
        xpub: Extended public key
        gap_limit: Gap limit for address discovery
        address_type: Address type override
        
    Returns:
        List of Bitcoin addresses
    """
    processor = XpubProcessor(xpub, address_type=address_type)
    used_addresses = processor.discover_used_addresses(gap_limit)
    
    return [addr_info['address'] for addr_info in used_addresses]


def validate_xpub(xpub: str) -> bool:
    """
    Validate if a string is a valid xpub key.
    
    Args:
        xpub: String to validate
        
    Returns:
        True if valid xpub, False otherwise
    """
    if not xpub or not isinstance(xpub, str):
        return False
    
    # Check prefix
    valid_prefixes = ['xpub', 'ypub', 'zpub', 'tpub', 'upub', 'vpub']  # Include testnet
    if not any(xpub.startswith(prefix) for prefix in valid_prefixes):
        return False
    
    # Check length (xpub should be 111 characters)
    if len(xpub) != 111:
        return False
    
    # Check if it's valid base58
    try:
        base58.b58decode(xpub)
        return True
    except Exception:
        return False
import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import ATTR_CODE

from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.exceptions import HomeAssistantError

from .const import COORDINATOR, DOMAIN
from .coordinator import EufySecurityDataUpdateCoordinator
from .entity import EufySecurityEntity
from .eufy_security_api.const import MessageField
from .eufy_security_api.metadata import Metadata
from .eufy_security_api.util import get_child_value

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Setup lock entities."""
    coordinator: EufySecurityDataUpdateCoordinator = hass.data[DOMAIN][COORDINATOR]
    properties = []
    for product in coordinator.devices.values():
        # Check for T85D0 (C30 basic lock) - it may not have locked property but is still a lock
        is_t85d0 = product.model and "T85D0" in product.model and "T85D0C" not in product.model
        
        if is_t85d0:
            _LOGGER.info(f"[T85D0 SUPPORT] Detected T85D0 lock: {product.name} (Model: {product.model})")
        
        if product.has(MessageField.LOCKED.value) is True:
            properties.append(product.metadata[MessageField.LOCKED.value])
            if is_t85d0:
                _LOGGER.info(f"[T85D0 SUPPORT] T85D0 lock '{product.name}' using standard 'locked' property")
        elif is_t85d0:
            # T85D0 might use a different property name, try to find it
            # Check common lock property names
            found_prop = None
            for prop_name in ["locked", "lockStatus", "lock"]:
                if product.has(prop_name):
                    if prop_name in product.metadata:
                        properties.append(product.metadata[prop_name])
                        found_prop = prop_name
                        break
            if found_prop:
                _LOGGER.info(f"[T85D0 SUPPORT] T85D0 lock '{product.name}' using alternative property: '{found_prop}'")
            else:
                # If no property found, log a warning but still try to create entity
                _LOGGER.warning(f"[T85D0 SUPPORT] T85D0 lock ({product.name}) found but no lock property detected. Available properties: {list(product.properties.keys())}")

    entities = [EufySecurityLock(coordinator, metadata) for metadata in properties]
    if entities:
        # Count T85D0 entities by checking product model
        t85d0_count = sum(
            1 for metadata in properties
            if metadata.product.model
            and "T85D0" in metadata.product.model
            and "T85D0C" not in metadata.product.model
        )
        if t85d0_count > 0:
            _LOGGER.info(f"[T85D0 SUPPORT] Created {t85d0_count} T85D0 lock entity/entities out of {len(entities)} total lock(s)")
    async_add_entities(entities)


class EufySecurityLock(LockEntity, EufySecurityEntity):
    """Base lock entity for integration"""

    def __init__(self, coordinator: EufySecurityDataUpdateCoordinator, metadata: Metadata) -> None:
        super().__init__(coordinator, metadata)
        self._attr_name = f"{self.product.name}"
        # Add diagnostic information for T85D0 identification
        if self._is_t85d0:
            _LOGGER.info(f"[T85D0 SUPPORT] Initialized T85D0 lock entity: {self.product.name} (Model: {self.product.model}, Safe Lock: {self.product.is_safe_lock})")

    @property
    def _is_t85d0(self) -> bool:
        """Check if this is a T85D0 (C30 basic lock, not T85D0C safe lock)"""
        return (
            self.product.model is not None
            and "T85D0" in self.product.model
            and "T85D0C" not in self.product.model
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for diagnostic purposes."""
        attrs = {
            "model": self.product.model,
            "is_safe_lock": self.product.is_safe_lock,
            "lock_type": "T85D0 (C30 Basic Lock)" if self._is_t85d0 else ("T85D0C (Safe Lock)" if "T85D0C" in (self.product.model or "") else "Standard Lock"),
            "property_name": self.metadata.name,
        }
        return attrs

    @property
    def is_locked(self):
        return get_child_value(self.product.properties, self.metadata.name)

    async def async_lock(self, **kwargs: Any) -> None:
        """Initiate lock call"""
        # T85D0 (C30 basic lock) is not a safe lock, allow locking
        if self.product.is_safe_lock is True and not self._is_t85d0:
            raise HomeAssistantError(f"Locking is not supported for lock ({self.product.name})")
        
        if self._is_t85d0:
            _LOGGER.info(f"[T85D0 SUPPORT] Locking T85D0: {self.product.name}")
        
        await self.product.set_property(self.metadata, True)
        
        if self._is_t85d0:
            _LOGGER.info(f"[T85D0 SUPPORT] Lock command sent successfully for T85D0: {self.product.name}")

    async def async_unlock(self, **kwargs: Any) -> None:
        """Initiate unlock call"""
        code = kwargs.get(ATTR_CODE, None)
        
        if self._is_t85d0:
            _LOGGER.info(f"[T85D0 SUPPORT] Unlocking T85D0: {self.product.name} (code provided: {code is not None})")
        
        # T85D0 (C30 basic lock) is not a safe lock, unlock without PIN
        if self.product.is_safe_lock is True and code is not None and not self._is_t85d0:
            # handling safe unlocking with pin (for T85D0C and other safe locks)
            if await self.product.unlock(code) is False:
                raise HomeAssistantError(f"PIN verification failed for lock ({self.product.name})")
        else:
            # Regular unlock for T85D0 and other non-safe locks
            await self.product.set_property(self.metadata, False)
        
        if self._is_t85d0:
            _LOGGER.info(f"[T85D0 SUPPORT] Unlock command sent successfully for T85D0: {self.product.name}")

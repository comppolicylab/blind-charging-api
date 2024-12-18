import logging
from typing import Protocol

from alligater import (
    Alligater,
    Arm,
    Feature,
    NoAssignment,
    ObjectLogger,
    Population,
    Rollout,
    Variant,
)
from alligater.feature import ExistingAssignment
from crocodsl import parse

from .config import config
from .db import Assignment, Gater

logger = logging.getLogger(__name__)


class AnyEntity(Protocol):
    @property
    def id(self) -> str: ...


def _get_assignment(feature: Feature, entity: AnyEntity) -> ExistingAssignment:
    """Fetch an assignment from the database.

    Args:
        feature (Feature): The feature to fetch.
        entity (Any): The entity to fetch.

    Returns:
        Tuple[str, str, datetime]: The feature and variant names.
    """
    session = config.experiments.store.driver.sync_session
    logger.debug("Reading alligater log from database")

    with session.begin() as tx:
        try:
            logger.debug(f"Fetching assignment for {entity}")
            assignment = (
                tx.query(Assignment)
                .filter(
                    Assignment.entity_type == type(entity).__name__,
                    Assignment.entity_id == entity.id,
                    Assignment.feature == feature.name,
                )
                .one_or_none()
            )
            if assignment:
                logger.debug(
                    f"Found assignment for {entity}: "
                    f"{assignment.variant} -> {assignment.value}"
                )
                return assignment.variant, assignment.value, assignment.ts
        except Exception as e:
            logger.error(f"Failed to fetch assignment: {e}")

    raise NoAssignment


def _save_assignment(obj):
    """Insert an assignment into the database.

    Args:
        obj (dict): The assignment object. (See `alligater.log`.)
    """
    session = config.experiments.store.driver.sync_session
    logger.debug("Writing alligater log to database")

    if obj["repeat"]:
        logger.debug("Skipping insert on exposure directly from Alligater")
        return
    if not obj["sticky"]:
        logger.debug("Skipping insert on non-sticky assignment")
        return

    with session.begin() as tx:
        try:
            logger.debug(f"Inserting assignment {obj['call_id']} into database")
            tx.add(
                Assignment(
                    entity_type=obj["entity"]["type"],
                    entity_id=obj["entity"]["value"].get("id", ""),
                    feature=obj["feature"]["name"],
                    variant=obj["variant"]["name"],
                    value=obj["assignment"],
                    ts=obj["ts"],
                    event_id=obj["call_id"],
                )
            )
            tx.commit()
        except Exception as e:
            logger.error(f"Failed to insert assignment: {e}")
            tx.rollback()

    if config.debug and obj["trace"]:
        logger.debug(f"Alligater event trace: {obj}")


def _load_config_from_db() -> str:
    """Load the feature flagging configuration from the database."""
    session = config.experiments.store.driver.sync_session
    logger.debug("Fetching latest alligater config from database")

    with session.begin() as tx:
        try:
            gater = tx.query(Gater).where(Gater.active).one()
            return gater.blob
        except Exception as e:
            logger.error(f"Failed to fetch alligater config: {e}")
            return ""


def init_gater(trace: bool = False) -> Alligater:
    """Initialize the feature flagging system.

    Args:
        trace (bool): Whether to trace the assignment events.

    Returns:
        Alligater: The feature flagging system.
    """
    feature_logger = ObjectLogger(_save_assignment, trace=trace, install_signals=False)

    gater = Alligater(
        logger=feature_logger,
        yaml=_load_config_from_db,
        sticky=_get_assignment,
        reload_interval=config.experiments.config_reload_interval,
        features=[
            Feature(
                "ft_blind_review",
                variants=[
                    Variant("blind", True),
                    Variant("control", False),
                    Variant("off", False),
                    Variant("on", True),
                ],
                rollouts=[
                    Rollout(
                        name="demo_experiment",
                        population=Population.Expression(
                            parse('$jurisdiction_id Eq "demo"')
                        ),
                        arms=[
                            Arm("blind", weight=0.5),
                            Arm("control", weight=0.5),
                        ],
                        sticky=True,
                    ),
                    Rollout(
                        name=Rollout.DEFAULT,
                        population=Population.DEFAULT,
                        arms=["on"],
                        sticky=False,
                    ),
                ],
            ),
        ],
    )

    return gater

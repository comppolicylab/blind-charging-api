from alligater import Alligater, Arm, Feature, Population, PrintLogger, Rollout, Variant
from crocodsl import parse

from .config import config

gater = Alligater(
    logger=PrintLogger(trace=config.debug),
    features=[
        Feature(
            "ft_blind_review",
            variants=[
                Variant("blind", True),
                Variant("control", False),
                Variant("off", False),
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
                    sticky=False,
                ),
                Rollout(
                    name=Rollout.DEFAULT,
                    population=Population.DEFAULT,
                    arms=["off"],
                    sticky=False,
                ),
            ],
        ),
    ],
)

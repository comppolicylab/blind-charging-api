import pytest
from bc2.core.common.name_map import IdToNameMap, NameToMaskMap
from fakeredis import FakeRedis

from app.server.case import CaseStore
from app.server.config import Config
from app.server.generated.models import HumanName


@pytest.mark.parametrize(
    "spec",
    [
        # Simple case with one stored role
        {
            "roles": {
                "sub1": "accused",
            },
            "masks": {},
            "names": {
                "sub1": {
                    "firstName": "jack",
                    "lastName": "doe",
                },
            },
            "expected_name_mask_map": NameToMaskMap(
                {
                    "jack doe": "Accused 1",
                }
            ),
            "expected_id_name_map": IdToNameMap(
                {
                    "sub1": "jack doe",
                }
            ),
        },
        # Simple case with one stored role and mask
        {
            "roles": {
                "sub1": "accused",
            },
            "masks": {
                "sub1": "Accused 1",
            },
            "names": {
                "sub1": {
                    "firstName": "jack",
                    "lastName": "doe",
                },
            },
            "expected_name_mask_map": NameToMaskMap(
                {
                    "jack doe": "Accused 1",
                }
            ),
            "expected_id_name_map": IdToNameMap(
                {
                    "sub1": "jack doe",
                }
            ),
        },
        # Combination of stored roles and masks
        {
            "roles": {
                "sub1": "accused",
                "sub2": "victim",
                "sub3": "accused",
                "sub4": "accused",
            },
            "masks": {
                "sub1": "Accused 1",
                "sub2": "Victim 1",
                "sub4": "Accused 2",
            },
            "names": {
                "sub1": {
                    "firstName": "jack",
                    "lastName": "doe",
                },
                "sub2": {
                    "firstName": "jane",
                    "lastName": "doe",
                },
                "sub3": {
                    "firstName": "james",
                    "lastName": "doe",
                },
                "sub4": {
                    "firstName": "jill",
                    "lastName": "doe",
                },
            },
            "expected_name_mask_map": NameToMaskMap(
                {
                    "jack doe": "Accused 1",
                    "jane doe": "Victim 1",
                    "james doe": "Accused 3",
                    "jill doe": "Accused 2",
                }
            ),
            "expected_id_name_map": IdToNameMap(
                {
                    "sub1": "jack doe",
                    "sub2": "jane doe",
                    "sub3": "james doe",
                    "sub4": "jill doe",
                }
            ),
        },
        # Weird case, but if we have a stored mask with a high number,
        # we should just continue the enumeration from there.
        {
            "roles": {
                "sub1": "accused",
                "sub2": "accused",
            },
            "masks": {
                "sub1": "Accused 100",
            },
            "names": {
                "sub1": {
                    "firstName": "jack",
                    "lastName": "doe",
                },
                "sub2": {
                    "firstName": "jane",
                    "lastName": "doe",
                },
            },
            "expected_name_mask_map": NameToMaskMap(
                {
                    "jack doe": "Accused 100",
                    "jane doe": "Accused 101",
                }
            ),
            "expected_id_name_map": IdToNameMap(
                {
                    "sub1": "jack doe",
                    "sub2": "jane doe",
                }
            ),
        },
    ],
)
async def test_get_name_mask_map(fake_redis_store: FakeRedis, config: Config, spec):
    for k, v in spec["roles"].items():
        fake_redis_store.hset("jur1:case1:role", k, v)

    for k, v in spec["masks"].items():
        fake_redis_store.hset("jur1:case1:mask", k, v)

    for k, v in spec["names"].items():
        fake_redis_store.set(
            f"jur1:case1:aliases:{k}:primary",
            HumanName(**v).model_dump_json(),
        )

    async with config.queue.store.driver() as store:
        async with store.tx() as tx:
            cs = CaseStore(tx)
            await cs.init("jur1", "case1")
            mask_info = await cs.get_mask_info()
            assert mask_info.get_name_mask_map() == spec["expected_name_mask_map"]
            assert mask_info.get_id_name_map() == spec["expected_id_name_map"]

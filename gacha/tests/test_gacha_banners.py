"""
Tests for gacha banners configuration and images integrity.
Ensures all JSON configs are valid and images exist.
"""

import json
from pathlib import Path
import pytest


# Paths
GACHA_DIR = Path(__file__).parent.parent
CONFIG_DIR = GACHA_DIR / "config" / "banners"
IMAGES_DIR = GACHA_DIR / "images"


class TestBannerConfigs:
    """Test banner configuration file structure and integrity."""

    @pytest.fixture
    def genshin_config(self):
        """Load Genshin Impact banner config."""
        with open(CONFIG_DIR / "genshin.json", "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def hsr_config(self):
        """Load Honkai: Star Rail banner config."""
        with open(CONFIG_DIR / "hsr.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def test_genshin_config_valid_json(self, genshin_config):
        """Test that Genshin config is valid JSON."""
        assert isinstance(genshin_config, dict)
        assert "code" in genshin_config
        assert "title" in genshin_config
        assert "cards" in genshin_config

    def test_hsr_config_valid_json(self, hsr_config):
        """Test that HSR config is valid JSON."""
        assert isinstance(hsr_config, dict)
        assert "code" in hsr_config
        assert "title" in hsr_config
        assert "cards" in hsr_config

    def test_genshin_config_structure(self, genshin_config):
        """Test Genshin config structure."""
        assert genshin_config["code"] == "genshin"
        assert isinstance(genshin_config["title"], str)
        assert genshin_config["cooldown_seconds"] > 0
        assert isinstance(genshin_config["cards"], list)
        assert len(genshin_config["cards"]) > 0

    def test_hsr_config_structure(self, hsr_config):
        """Test HSR config structure."""
        assert hsr_config["code"] == "hsr"
        assert isinstance(hsr_config["title"], str)
        assert hsr_config["cooldown_seconds"] > 0
        assert isinstance(hsr_config["cards"], list)
        assert len(hsr_config["cards"]) > 0

    def test_genshin_card_structure(self, genshin_config):
        """Test that all Genshin cards have required fields."""
        required_fields = {
            "code",
            "name",
            "rarity",
            "points",
            "primogems",
            "adventure_xp",
            "image_url",
            "region_code",
            "element_code",
            "weight",
        }
        for card in genshin_config["cards"]:
            assert isinstance(card, dict)
            assert required_fields.issubset(card.keys()), f"Card {card.get('code')} missing fields"
            assert isinstance(card["code"], str) and card["code"]
            assert isinstance(card["name"], str) and card["name"]
            assert card["rarity"] in {"common", "rare", "epic", "legendary", "mythic"}
            assert card["points"] >= 0
            assert card["primogems"] >= 0
            assert card["adventure_xp"] >= 0
            assert card["weight"] > 0
            assert isinstance(card["region_code"], str) and card["region_code"]
            assert isinstance(card["element_code"], str) and card["element_code"]
            assert card["image_url"].startswith("/images/genshin/")

    def test_hsr_card_structure(self, hsr_config):
        """Test that all HSR cards have required base fields and optional metadata."""
        required_fields = {"code", "name", "rarity", "points", "primogems", "adventure_xp", "image_url", "weight"}
        for card in hsr_config["cards"]:
            assert isinstance(card, dict)
            assert required_fields.issubset(card.keys()), f"Card {card.get('code')} missing fields"
            assert isinstance(card["code"], str) and card["code"]
            assert isinstance(card["name"], str) and card["name"]
            assert card["rarity"] in {"common", "rare", "epic", "legendary", "mythic"}
            assert card["points"] >= 0
            assert card["primogems"] >= 0
            assert card["adventure_xp"] >= 0
            assert card["weight"] > 0
            if "region_code" in card:
                assert isinstance(card["region_code"], str) and card["region_code"]
            if "element_code" in card:
                assert isinstance(card["element_code"], str) and card["element_code"]
            assert card["image_url"].startswith("/images/hsr/")

    def test_genshin_unique_codes(self, genshin_config):
        """Test that all Genshin character codes are unique."""
        codes = [card["code"] for card in genshin_config["cards"]]
        assert len(codes) == len(set(codes)), "Duplicate card codes found"

    def test_hsr_unique_codes(self, hsr_config):
        """Test that all HSR character codes are unique."""
        codes = [card["code"] for card in hsr_config["cards"]]
        assert len(codes) == len(set(codes)), "Duplicate card codes found"


class TestImageFiles:
    """Test that image files exist for all cards."""

    @pytest.fixture
    def genshin_config(self):
        """Load Genshin Impact banner config."""
        with open(CONFIG_DIR / "genshin.json", "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def hsr_config(self):
        """Load Honkai: Star Rail banner config."""
        with open(CONFIG_DIR / "hsr.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def test_genshin_images_exist(self, genshin_config):
        """Test that all Genshin card images exist."""
        missing_images = []
        for card in genshin_config["cards"]:
            image_filename = card["image_url"].replace("/images/genshin/", "")
            image_path = IMAGES_DIR / "genshin" / image_filename
            if not image_path.exists():
                missing_images.append((card["code"], card["image_url"]))

        assert not missing_images, f"Missing Genshin images: {missing_images}"

    def test_hsr_images_exist(self, hsr_config):
        """Test that all HSR card images exist."""
        missing_images = []
        for card in hsr_config["cards"]:
            image_filename = card["image_url"].replace("/images/hsr/", "")
            image_path = IMAGES_DIR / "hsr" / image_filename
            if not image_path.exists():
                missing_images.append((card["code"], card["image_url"]))

        assert not missing_images, f"Missing HSR images: {missing_images}"

    def test_genshin_no_duplicate_images_except_variants(self, genshin_config):
        """Test that Genshin doesn't have duplicate image files (except intentional variants)."""
        images = [card["image_url"] for card in genshin_config["cards"]]
        # Allow intentional duplicates like flins and flins2
        images_without_variants = [img for img in images if not any(x in img for x in ["2", "variant"])]
        assert len(images_without_variants) == len(set(images_without_variants)), \
            "Accidental duplicate images found"

    def test_hsr_no_duplicate_images(self, hsr_config):
        """Test that HSR doesn't have duplicate image files."""
        images = [card["image_url"] for card in hsr_config["cards"]]
        assert len(images) == len(set(images)), "Duplicate images found in HSR"


class TestRarityDistribution:
    """Test rarity distribution makes sense."""

    @pytest.fixture
    def genshin_config(self):
        """Load Genshin Impact banner config."""
        with open(CONFIG_DIR / "genshin.json", "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def hsr_config(self):
        """Load Honkai: Star Rail banner config."""
        with open(CONFIG_DIR / "hsr.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def test_genshin_rarity_distribution(self, genshin_config):
        """Test Genshin tracks higher-tier rarities via epic/legendary/mythic."""
        rarities = [card["rarity"] for card in genshin_config["cards"]]
        epic_count = rarities.count("epic")
        legendary_count = rarities.count("legendary")
        mythic_count = rarities.count("mythic")

        # Genshin should keep higher-tier rarities in the banner pool.
        assert epic_count > 0, "No epic cards"
        assert legendary_count > 0, "No legendary cards"
        assert epic_count + legendary_count + mythic_count == len(rarities), "Unexpected lower-tier rarities remain in Genshin"

    def test_genshin_rewards_by_rarity(self, genshin_config):
        """Test that card rewards increase with rarity."""
        cards_by_rarity = {}
        for card in genshin_config["cards"]:
            rarity = card["rarity"]
            if rarity not in cards_by_rarity:
                cards_by_rarity[rarity] = []
            cards_by_rarity[rarity].append(card)

        if "epic" in cards_by_rarity and "legendary" in cards_by_rarity:
            avg_epic_points = sum(c["points"] for c in cards_by_rarity["epic"]) / len(cards_by_rarity["epic"])
            avg_legendary_points = sum(c["points"] for c in cards_by_rarity["legendary"]) / len(cards_by_rarity["legendary"])
            assert avg_epic_points < avg_legendary_points, "Epic cards should have lower rewards than legendary"
        if "legendary" in cards_by_rarity and "mythic" in cards_by_rarity:
            avg_legendary_points = sum(c["points"] for c in cards_by_rarity["legendary"]) / len(cards_by_rarity["legendary"])
            avg_mythic_points = sum(c["points"] for c in cards_by_rarity["mythic"]) / len(cards_by_rarity["mythic"])
            assert avg_legendary_points < avg_mythic_points, "Legendary cards should have lower rewards than mythic"


class TestWeights:
    """Test card weight distribution."""

    @pytest.fixture
    def genshin_config(self):
        """Load Genshin Impact banner config."""
        with open(CONFIG_DIR / "genshin.json", "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def hsr_config(self):
        """Load Honkai: Star Rail banner config."""
        with open(CONFIG_DIR / "hsr.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def test_genshin_weights_positive(self, genshin_config):
        """Test that all Genshin card weights are positive."""
        for card in genshin_config["cards"]:
            assert card["weight"] > 0, f"Card {card['code']} has non-positive weight"

    def test_hsr_weights_positive(self, hsr_config):
        """Test that all HSR card weights are positive."""
        for card in hsr_config["cards"]:
            assert card["weight"] > 0, f"Card {card['code']} has non-positive weight"

    def test_genshin_weight_distribution(self, genshin_config):
        """Test that weight distribution is reasonable in Genshin."""
        weights_by_rarity = {}
        for card in genshin_config["cards"]:
            rarity = card["rarity"]
            if rarity not in weights_by_rarity:
                weights_by_rarity[rarity] = []
            weights_by_rarity[rarity].append(card["weight"])

        if "epic" in weights_by_rarity and "legendary" in weights_by_rarity:
            avg_common = sum(weights_by_rarity["epic"]) / len(weights_by_rarity["epic"])
            avg_legendary = sum(weights_by_rarity["legendary"]) / len(weights_by_rarity["legendary"])
            assert avg_common > avg_legendary, "Epic cards should have higher weight than legendary"
        if "legendary" in weights_by_rarity and "mythic" in weights_by_rarity:
            total_legendary = sum(weights_by_rarity["legendary"])
            total_mythic = sum(weights_by_rarity["mythic"])
            assert total_legendary > total_mythic, "Legendary total weight should stay higher than mythic"


class TestStatistics:
    """Test and display banner statistics."""

    @pytest.fixture
    def genshin_config(self):
        """Load Genshin Impact banner config."""
        with open(CONFIG_DIR / "genshin.json", "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def hsr_config(self):
        """Load Honkai: Star Rail banner config."""
        with open(CONFIG_DIR / "hsr.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def test_genshin_statistics(self, genshin_config, capsys):
        """Test and display Genshin statistics."""
        print("\n=== Genshin Impact Statistics ===")
        print(f"Total cards: {len(genshin_config['cards'])}")

        rarities = {}
        for card in genshin_config["cards"]:
            rarity = card["rarity"]
            if rarity not in rarities:
                rarities[rarity] = 0
            rarities[rarity] += 1

        for rarity in ["common", "rare", "epic", "legendary", "mythic"]:
            count = rarities.get(rarity, 0)
            print(f"  {rarity}: {count}")

        # Verify data is reasonable
        assert len(genshin_config["cards"]) > 0

    def test_hsr_statistics(self, hsr_config, capsys):
        """Test and display HSR statistics."""
        print("\n=== Honkai: Star Rail Statistics ===")
        print(f"Total cards: {len(hsr_config['cards'])}")

        rarities = {}
        for card in hsr_config["cards"]:
            rarity = card["rarity"]
            if rarity not in rarities:
                rarities[rarity] = 0
            rarities[rarity] += 1

        for rarity in ["common", "rare", "epic", "legendary", "mythic"]:
            count = rarities.get(rarity, 0)
            print(f"  {rarity}: {count}")

        # Verify data is reasonable
        assert len(hsr_config["cards"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])


class TestZzzBannerConfig:
    """Zenless Zone Zero banner: roster, odds mirror HSR (S/A) plus Genshin mythic (Pyrois), placeholder fallback."""

    @pytest.fixture
    def zzz_config(self):
        with open(CONFIG_DIR / "zzz.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def test_zzz_banner_header(self, zzz_config):
        assert zzz_config["code"] == "zzz"
        assert zzz_config["title"] == "Zenless Zone Zero"
        assert zzz_config["cooldown_seconds"] == 3600

    def test_zzz_roster_has_46_s_rank_and_13_a_rank_agents(self, zzz_config):
        rarities = [card["rarity"] for card in zzz_config["cards"]]
        assert rarities.count("legendary") == 46
        assert rarities.count("epic") == 13
        assert rarities.count("mythic") == 1
        assert len(rarities) == 60

    def test_zzz_pyrois_is_the_only_mythic_special_agent(self, zzz_config):
        mythic = [card for card in zzz_config["cards"] if card["rarity"] == "mythic"]
        assert [card["code"] for card in mythic] == ["pyrois"]
        assert mythic[0]["name"] == "Пироис (Pyrois)"
        assert mythic[0]["points"] == 20000
        assert mythic[0]["primogems"] == 44
        assert mythic[0]["adventure_xp"] == 440
        assert mythic[0]["weight"] == 5

    def test_zzz_card_codes_are_unique(self, zzz_config):
        codes = [card["code"] for card in zzz_config["cards"]]
        assert len(codes) == len(set(codes))

    def test_zzz_rarity_values_mirror_hsr(self, zzz_config):
        expected = {
            "legendary": {"points": 11000, "primogems": 22, "adventure_xp": 200, "weight": 1},
            "epic": {"points": 5000, "primogems": 16, "adventure_xp": 150, "weight": 6},
            "mythic": {"points": 20000, "primogems": 44, "adventure_xp": 440, "weight": 5},
        }
        for card in zzz_config["cards"]:
            values = expected[card["rarity"]]
            assert card["points"] == values["points"], card["code"]
            assert card["primogems"] == values["primogems"], card["code"]
            assert card["adventure_xp"] == values["adventure_xp"], card["code"]
            assert card["weight"] == values["weight"], card["code"]

    def test_zzz_image_paths_follow_banner_convention(self, zzz_config):
        for card in zzz_config["cards"]:
            assert card["image_url"] == f"/images/zzz/{card['code']}.jpg"

    def test_missing_zzz_artwork_falls_back_to_placeholder(self):
        from gacha_service.application.catalog import PLACEHOLDER_IMAGE_URL, resolve_card_image_url

        assert resolve_card_image_url("/images/zzz/not_uploaded_yet.jpg") == PLACEHOLDER_IMAGE_URL
        assert (IMAGES_DIR / "placeholder.jpg").is_file()
        assert resolve_card_image_url(PLACEHOLDER_IMAGE_URL) == PLACEHOLDER_IMAGE_URL
        assert resolve_card_image_url("https://example.com/card.png") == "https://example.com/card.png"

    def test_zzz_config_keeps_canonical_artwork_urls(self, zzz_config):
        # The placeholder is applied at response time only, so stored pulls keep the real path.
        from gacha_service.application.catalog import get_banner_config

        rina = next(card for card in get_banner_config("zzz").cards if card.code == "rina")
        assert rina.image_url == "/images/zzz/rina.jpg"


def test_profile_rank_labels_per_banner() -> None:
    from gacha_service.domain.banner_profiles import get_banner_profile

    assert get_banner_profile("genshin").profile_rank_label == "Ранг"
    assert get_banner_profile("hsr").profile_rank_label == "Освоение"
    assert get_banner_profile("zzz").profile_rank_label == "Уровень Интернота"

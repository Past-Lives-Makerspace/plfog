"""Eventbrite's event categories and subcategories (#716), kept in code.

Taken from Eventbrite's ``GET /categories/`` and ``GET /subcategories/`` on 2026-10-08, so the
composer never calls Eventbrite to draw its dropdowns. Eventbrite adds a category rarely; when it
does, add the row here (the choices change needs a migration, like any other). A subcategory
belongs to exactly one category, :data:`SUBCATEGORY_PARENT` says which, and 199 Other has none.
"""

from django.db import models

# Every listed class is a "Class, Training, or Workshop" (GET /formats/, id 9).
CLASS_FORMAT_ID = "9"


class EventbriteCategory(models.TextChoices):
    BUSINESS_PROFESSIONAL = "101", "Business & Professional"
    SCIENCE_TECHNOLOGY = "102", "Science & Technology"
    MUSIC = "103", "Music"
    FILM_MEDIA_ENTERTAINMENT = "104", "Film, Media & Entertainment"
    PERFORMING_VISUAL_ARTS = "105", "Performing & Visual Arts"
    FASHION_BEAUTY = "106", "Fashion & Beauty"
    HEALTH_WELLNESS = "107", "Health & Wellness"
    SPORTS_FITNESS = "108", "Sports & Fitness"
    TRAVEL_OUTDOOR = "109", "Travel & Outdoor"
    FOOD_DRINK = "110", "Food & Drink"
    CHARITY_CAUSES = "111", "Charity & Causes"
    GOVERNMENT_POLITICS = "112", "Government & Politics"
    COMMUNITY_CULTURE = "113", "Community & Culture"
    RELIGION_SPIRITUALITY = "114", "Religion & Spirituality"
    FAMILY_EDUCATION = "115", "Family & Education"
    SEASONAL_HOLIDAY = "116", "Seasonal & Holiday"
    HOME_LIFESTYLE = "117", "Home & Lifestyle"
    AUTO_BOAT_AIR = "118", "Auto, Boat & Air"
    HOBBIES_SPECIAL_INTEREST = "119", "Hobbies & Special Interest"
    SCHOOL_ACTIVITIES = "120", "School Activities"
    OTHER = "199", "Other"


class EventbriteSubcategory(models.TextChoices):
    """Member names end in the ID: "Other" alone is 19 different subcategories."""

    STARTUPS_SMALL_BUSINESS_1001 = "1001", "Startups & Small Business"
    FINANCE_1002 = "1002", "Finance"
    ENVIRONMENT_SUSTAINABILITY_1003 = "1003", "Environment & Sustainability"
    EDUCATORS_1004 = "1004", "Educators"
    REAL_ESTATE_1005 = "1005", "Real Estate"
    NON_PROFIT_NGOS_1006 = "1006", "Non Profit & NGOs"
    SALES_MARKETING_1007 = "1007", "Sales & Marketing"
    MEDIA_1008 = "1008", "Media"
    DESIGN_1009 = "1009", "Design"
    CAREER_1010 = "1010", "Career"
    INVESTMENT_1011 = "1011", "Investment"
    OTHER_1999 = "1999", "Other"
    MEDICINE_2001 = "2001", "Medicine"
    SCIENCE_2002 = "2002", "Science"
    BIOTECH_2003 = "2003", "Biotech"
    HIGH_TECH_2004 = "2004", "High Tech"
    MOBILE_2005 = "2005", "Mobile"
    SOCIAL_MEDIA_2006 = "2006", "Social Media"
    ROBOTICS_2007 = "2007", "Robotics"
    OTHER_2999 = "2999", "Other"
    ALTERNATIVE_3001 = "3001", "Alternative"
    BLUES_JAZZ_3002 = "3002", "Blues & Jazz"
    CLASSICAL_3003 = "3003", "Classical"
    COUNTRY_3004 = "3004", "Country"
    CULTURAL_3005 = "3005", "Cultural"
    EDM_ELECTRONIC_3006 = "3006", "EDM / Electronic"
    FOLK_3007 = "3007", "Folk"
    HIP_HOP_RAP_3008 = "3008", "Hip Hop / Rap"
    INDIE_3009 = "3009", "Indie"
    LATIN_3010 = "3010", "Latin"
    METAL_3011 = "3011", "Metal"
    OPERA_3012 = "3012", "Opera"
    POP_3013 = "3013", "Pop"
    R_B_3014 = "3014", "R&B"
    REGGAE_3015 = "3015", "Reggae"
    RELIGIOUS_SPIRITUAL_3016 = "3016", "Religious/Spiritual"
    ROCK_3017 = "3017", "Rock"
    TOP_40_3018 = "3018", "Top 40"
    ACOUSTIC_3019 = "3019", "Acoustic"
    AMERICANA_3020 = "3020", "Americana"
    BLUEGRASS_3021 = "3021", "Bluegrass"
    BLUES_3022 = "3022", "Blues"
    DJ_DANCE_3023 = "3023", "DJ/Dance"
    EDM_3024 = "3024", "EDM"
    ELECTRONIC_3025 = "3025", "Electronic"
    EXPERIMENTAL_3026 = "3026", "Experimental"
    JAZZ_3027 = "3027", "Jazz"
    PSYCHEDELIC_3028 = "3028", "Psychedelic"
    PUNK_HARDCORE_3029 = "3029", "Punk/Hardcore"
    SINGER_SONGWRITER_3030 = "3030", "Singer/Songwriter"
    WORLD_3031 = "3031", "World"
    OTHER_3999 = "3999", "Other"
    TV_4001 = "4001", "TV"
    FILM_4002 = "4002", "Film"
    ANIME_4003 = "4003", "Anime"
    GAMING_4004 = "4004", "Gaming"
    COMICS_4005 = "4005", "Comics"
    ADULT_4006 = "4006", "Adult"
    COMEDY_4007 = "4007", "Comedy"
    OTHER_4999 = "4999", "Other"
    THEATRE_5001 = "5001", "Theatre"
    MUSICAL_5002 = "5002", "Musical"
    BALLET_5003 = "5003", "Ballet"
    DANCE_5004 = "5004", "Dance"
    OPERA_5005 = "5005", "Opera"
    ORCHESTRA_5006 = "5006", "Orchestra"
    CRAFT_5007 = "5007", "Craft"
    FINE_ART_5008 = "5008", "Fine Art"
    LITERARY_ARTS_5009 = "5009", "Literary Arts"
    COMEDY_5010 = "5010", "Comedy"
    SCULPTURE_5011 = "5011", "Sculpture"
    PAINTING_5012 = "5012", "Painting"
    DESIGN_5013 = "5013", "Design"
    JEWELRY_5014 = "5014", "Jewelry"
    OTHER_5999 = "5999", "Other"
    FASHION_6001 = "6001", "Fashion"
    ACCESSORIES_6002 = "6002", "Accessories"
    BRIDAL_6003 = "6003", "Bridal"
    BEAUTY_6004 = "6004", "Beauty"
    OTHER_6999 = "6999", "Other"
    PERSONAL_HEALTH_7001 = "7001", "Personal health"
    MENTAL_HEALTH_7002 = "7002", "Mental health"
    MEDICAL_7003 = "7003", "Medical"
    SPA_7004 = "7004", "Spa"
    YOGA_7005 = "7005", "Yoga"
    OTHER_7999 = "7999", "Other"
    RUNNING_8001 = "8001", "Running"
    WALKING_8002 = "8002", "Walking"
    CYCLING_8003 = "8003", "Cycling"
    MOUNTAIN_BIKING_8004 = "8004", "Mountain Biking"
    OBSTACLES_8005 = "8005", "Obstacles"
    BASKETBALL_8006 = "8006", "Basketball"
    FOOTBALL_8007 = "8007", "Football"
    BASEBALL_8008 = "8008", "Baseball"
    SOCCER_8009 = "8009", "Soccer"
    GOLF_8010 = "8010", "Golf"
    VOLLEYBALL_8011 = "8011", "Volleyball"
    TENNIS_8012 = "8012", "Tennis"
    SWIMMING_WATER_SPORTS_8013 = "8013", "Swimming & Water Sports"
    HOCKEY_8014 = "8014", "Hockey"
    MOTORSPORTS_8015 = "8015", "Motorsports"
    FIGHTING_MARTIAL_ARTS_8016 = "8016", "Fighting & Martial Arts"
    SNOW_SPORTS_8017 = "8017", "Snow Sports"
    RUGBY_8018 = "8018", "Rugby"
    YOGA_8019 = "8019", "Yoga"
    EXERCISE_8020 = "8020", "Exercise"
    SOFTBALL_8021 = "8021", "Softball"
    WRESTLING_8022 = "8022", "Wrestling"
    LACROSSE_8023 = "8023", "Lacrosse"
    CHEER_8024 = "8024", "Cheer"
    CAMPS_8025 = "8025", "Camps"
    WEIGHTLIFTING_8026 = "8026", "Weightlifting"
    TRACK_FIELD_8027 = "8027", "Track & Field"
    OTHER_8999 = "8999", "Other"
    HIKING_9001 = "9001", "Hiking"
    RAFTING_9002 = "9002", "Rafting"
    KAYAKING_9003 = "9003", "Kayaking"
    CANOEING_9004 = "9004", "Canoeing"
    CLIMBING_9005 = "9005", "Climbing"
    TRAVEL_9006 = "9006", "Travel"
    OTHER_9999 = "9999", "Other"
    BEER_10001 = "10001", "Beer"
    WINE_10002 = "10002", "Wine"
    FOOD_10003 = "10003", "Food"
    SPIRITS_10004 = "10004", "Spirits"
    OTHER_10999 = "10999", "Other"
    ANIMAL_WELFARE_11001 = "11001", "Animal Welfare"
    ENVIRONMENT_11002 = "11002", "Environment"
    HEALTHCARE_11003 = "11003", "Healthcare"
    HUMAN_RIGHTS_11004 = "11004", "Human Rights"
    INTERNATIONAL_AID_11005 = "11005", "International Aid"
    POVERTY_11006 = "11006", "Poverty"
    DISASTER_RELIEF_11007 = "11007", "Disaster Relief"
    EDUCATION_11008 = "11008", "Education"
    OTHER_11999 = "11999", "Other"
    REPUBLICAN_PARTY_12001 = "12001", "Republican Party"
    DEMOCRATIC_PARTY_12002 = "12002", "Democratic Party"
    OTHER_PARTY_12003 = "12003", "Other Party"
    NON_PARTISAN_12004 = "12004", "Non-partisan"
    FEDERAL_GOVERNMENT_12005 = "12005", "Federal Government"
    STATE_GOVERNMENT_12006 = "12006", "State Government"
    COUNTY_MUNICIPAL_GOVERNMENT_12007 = "12007", "County/Municipal Government "
    MILITARY_12008 = "12008", "Military"
    INTERNATIONAL_AFFAIRS_12009 = "12009", "International Affairs"
    NATIONAL_SECURITY_12010 = "12010", "National Security"
    OTHER_12999 = "12999", "Other"
    STATE_13001 = "13001", "State"
    COUNTY_13002 = "13002", "County"
    CITY_TOWN_13003 = "13003", "City/Town"
    LGBT_13004 = "13004", "LGBT"
    MEDIEVAL_13005 = "13005", "Medieval"
    RENAISSANCE_13006 = "13006", "Renaissance"
    HERITAGE_13007 = "13007", "Heritage"
    NATIONALITY_13008 = "13008", "Nationality"
    LANGUAGE_13009 = "13009", "Language"
    HISTORIC_13010 = "13010", "Historic"
    OTHER_13999 = "13999", "Other"
    CHRISTIANITY_14001 = "14001", "Christianity"
    JUDAISM_14002 = "14002", "Judaism"
    ISLAM_14003 = "14003", "Islam"
    MORMONISM_14004 = "14004", "Mormonism"
    BUDDHISM_14005 = "14005", "Buddhism"
    SIKHISM_14006 = "14006", "Sikhism"
    EASTERN_RELIGION_14007 = "14007", "Eastern Religion"
    MYSTICISM_AND_OCCULT_14008 = "14008", "Mysticism and Occult"
    NEW_AGE_14009 = "14009", "New Age"
    ATHEISM_14010 = "14010", "Atheism"
    AGNOSTICISM_14011 = "14011", "Agnosticism"
    UNAFFILIATED_14012 = "14012", "Unaffiliated"
    HINDUISM_14013 = "14013", "Hinduism"
    FOLK_RELIGIONS_14014 = "14014", "Folk Religions"
    SHINTOISM_14015 = "14015", "Shintoism"
    OTHER_14099 = "14099", "Other"
    EDUCATION_15001 = "15001", "Education"
    ALUMNI_15002 = "15002", "Alumni"
    PARENTING_15003 = "15003", "Parenting"
    BABY_15004 = "15004", "Baby"
    CHILDREN_YOUTH_15005 = "15005", "Children & Youth "
    PARENTS_ASSOCIATION_15006 = "15006", "Parents Association"
    REUNION_15007 = "15007", "Reunion"
    SENIOR_CITIZEN_15008 = "15008", "Senior Citizen"
    OTHER_15999 = "15999", "Other"
    ST_PATRICKS_DAY_16001 = "16001", "St Patricks Day"
    EASTER_16002 = "16002", "Easter"
    INDEPENDENCE_DAY_16003 = "16003", "Independence Day"
    HALLOWEEN_HAUNT_16004 = "16004", "Halloween/Haunt"
    THANKSGIVING_16005 = "16005", "Thanksgiving"
    CHRISTMAS_16006 = "16006", "Christmas"
    CHANNUKAH_16007 = "16007", "Channukah"
    FALL_EVENTS_16008 = "16008", "Fall events"
    NEW_YEARS_EVE_16009 = "16009", "New Years Eve"
    OTHER_16999 = "16999", "Other"
    DATING_17001 = "17001", "Dating"
    PETS_ANIMALS_17002 = "17002", "Pets & Animals"
    HOME_GARDEN_17003 = "17003", "Home & Garden"
    OTHER_17999 = "17999", "Other"
    AUTO_18001 = "18001", "Auto"
    MOTORCYCLE_ATV_18002 = "18002", "Motorcycle/ATV"
    BOAT_18003 = "18003", "Boat"
    AIR_18004 = "18004", "Air"
    OTHER_18999 = "18999", "Other"
    ANIME_COMICS_19001 = "19001", "Anime/Comics"
    GAMING_19002 = "19002", "Gaming"
    DIY_19003 = "19003", "DIY"
    PHOTOGRAPHY_19004 = "19004", "Photography"
    KNITTING_19005 = "19005", "Knitting"
    BOOKS_19006 = "19006", "Books"
    ADULT_19007 = "19007", "Adult"
    DRAWING_PAINTING_19008 = "19008", "Drawing & Painting"
    OTHER_19999 = "19999", "Other"
    DINNER_20001 = "20001", "Dinner"
    FUND_RAISER_20002 = "20002", "Fund Raiser"
    RAFFLE_20003 = "20003", "Raffle"
    AFTER_SCHOOL_CARE_20004 = "20004", "After School Care"
    PARKING_20005 = "20005", "Parking"
    PUBLIC_SPEAKER_20006 = "20006", "Public Speaker"


# Subcategory ID to its parent category ID.
SUBCATEGORY_PARENT: dict[str, str] = {
    "1001": "101",
    "1002": "101",
    "1003": "101",
    "1004": "101",
    "1005": "101",
    "1006": "101",
    "1007": "101",
    "1008": "101",
    "1009": "101",
    "1010": "101",
    "1011": "101",
    "1999": "101",
    "2001": "102",
    "2002": "102",
    "2003": "102",
    "2004": "102",
    "2005": "102",
    "2006": "102",
    "2007": "102",
    "2999": "102",
    "3001": "103",
    "3002": "103",
    "3003": "103",
    "3004": "103",
    "3005": "103",
    "3006": "103",
    "3007": "103",
    "3008": "103",
    "3009": "103",
    "3010": "103",
    "3011": "103",
    "3012": "103",
    "3013": "103",
    "3014": "103",
    "3015": "103",
    "3016": "103",
    "3017": "103",
    "3018": "103",
    "3019": "103",
    "3020": "103",
    "3021": "103",
    "3022": "103",
    "3023": "103",
    "3024": "103",
    "3025": "103",
    "3026": "103",
    "3027": "103",
    "3028": "103",
    "3029": "103",
    "3030": "103",
    "3031": "103",
    "3999": "103",
    "4001": "104",
    "4002": "104",
    "4003": "104",
    "4004": "104",
    "4005": "104",
    "4006": "104",
    "4007": "104",
    "4999": "104",
    "5001": "105",
    "5002": "105",
    "5003": "105",
    "5004": "105",
    "5005": "105",
    "5006": "105",
    "5007": "105",
    "5008": "105",
    "5009": "105",
    "5010": "105",
    "5011": "105",
    "5012": "105",
    "5013": "105",
    "5014": "105",
    "5999": "105",
    "6001": "106",
    "6002": "106",
    "6003": "106",
    "6004": "106",
    "6999": "106",
    "7001": "107",
    "7002": "107",
    "7003": "107",
    "7004": "107",
    "7005": "107",
    "7999": "107",
    "8001": "108",
    "8002": "108",
    "8003": "108",
    "8004": "108",
    "8005": "108",
    "8006": "108",
    "8007": "108",
    "8008": "108",
    "8009": "108",
    "8010": "108",
    "8011": "108",
    "8012": "108",
    "8013": "108",
    "8014": "108",
    "8015": "108",
    "8016": "108",
    "8017": "108",
    "8018": "108",
    "8019": "108",
    "8020": "108",
    "8021": "108",
    "8022": "108",
    "8023": "108",
    "8024": "108",
    "8025": "108",
    "8026": "108",
    "8027": "108",
    "8999": "108",
    "9001": "109",
    "9002": "109",
    "9003": "109",
    "9004": "109",
    "9005": "109",
    "9006": "109",
    "9999": "109",
    "10001": "110",
    "10002": "110",
    "10003": "110",
    "10004": "110",
    "10999": "110",
    "11001": "111",
    "11002": "111",
    "11003": "111",
    "11004": "111",
    "11005": "111",
    "11006": "111",
    "11007": "111",
    "11008": "111",
    "11999": "111",
    "12001": "112",
    "12002": "112",
    "12003": "112",
    "12004": "112",
    "12005": "112",
    "12006": "112",
    "12007": "112",
    "12008": "112",
    "12009": "112",
    "12010": "112",
    "12999": "112",
    "13001": "113",
    "13002": "113",
    "13003": "113",
    "13004": "113",
    "13005": "113",
    "13006": "113",
    "13007": "113",
    "13008": "113",
    "13009": "113",
    "13010": "113",
    "13999": "113",
    "14001": "114",
    "14002": "114",
    "14003": "114",
    "14004": "114",
    "14005": "114",
    "14006": "114",
    "14007": "114",
    "14008": "114",
    "14009": "114",
    "14010": "114",
    "14011": "114",
    "14012": "114",
    "14013": "114",
    "14014": "114",
    "14015": "114",
    "14099": "114",
    "15001": "115",
    "15002": "115",
    "15003": "115",
    "15004": "115",
    "15005": "115",
    "15006": "115",
    "15007": "115",
    "15008": "115",
    "15999": "115",
    "16001": "116",
    "16002": "116",
    "16003": "116",
    "16004": "116",
    "16005": "116",
    "16006": "116",
    "16007": "116",
    "16008": "116",
    "16009": "116",
    "16999": "116",
    "17001": "117",
    "17002": "117",
    "17003": "117",
    "17999": "117",
    "18001": "118",
    "18002": "118",
    "18003": "118",
    "18004": "118",
    "18999": "118",
    "19001": "119",
    "19002": "119",
    "19003": "119",
    "19004": "119",
    "19005": "119",
    "19006": "119",
    "19007": "119",
    "19008": "119",
    "19999": "119",
    "20001": "120",
    "20002": "120",
    "20003": "120",
    "20004": "120",
    "20005": "120",
    "20006": "120",
}


def subcategory_fits(category: str, subcategory: str) -> bool:
    """Whether ``subcategory`` may be sent with ``category``: none at all, or one of its children."""
    return not subcategory or SUBCATEGORY_PARENT[subcategory] == category

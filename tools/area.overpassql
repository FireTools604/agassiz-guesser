/*
  Agassiz Guesser play-area source data.
  Run at https://overpass-turbo.eu (Export > raw OSM data) or:
    curl -sS -X POST https://overpass-api.de/api/interpreter --data-urlencode data@tools/area.overpassql -o tools/osm.json
  Then: python3 tools/build_area.py tools/osm.json area.json
*/
[out:json][timeout:180];

// District of Kent boundary
rel["boundary"="administrative"]["admin_level"="8"]["name"~"^(District of )?Kent$"](49.1,-122.0,49.5,-121.5)->.kent;

// Exclusions: Village of Harrison Hot Springs + First Nation reserves near the District
rel["boundary"="administrative"]["name"~"Harrison Hot Springs"](49.1,-122.0,49.5,-121.5)->.hhs;
(
  rel["boundary"="aboriginal_lands"](49.1,-122.0,49.5,-121.5);
  way["boundary"="aboriginal_lands"](49.1,-122.0,49.5,-121.5);
)->.res;

// Drivable roads inside the District, used to weight random point selection
.kent map_to_area ->.a;
way(area.a)["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|unclassified|residential|living_street|service)$"]["access"!~"^(private|no)$"]->.roads;

(.kent; .hhs; .res;);
out body geom;
.roads out body geom;

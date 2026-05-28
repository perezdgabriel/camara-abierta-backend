# BCN Linked Open Data API Specification: Parliamentary Data Extraction
## Technical Reference Document for Code Generation Agents
**Target System:** Cámara Abierta Data Extraction Pipeline
**Source Platform:** Biblioteca del Congreso Nacional de Chile (BCN) Linked Open Data Endpoint

---

## 1. Architectural Overview & Paradigm Shift

The BCN Open Data platform does **not** expose a standard REST or GraphQL API. Instead, it utilizes **Semantic Web / Linked Open Data (LOD)** standards backed by a Graph Database (Triple Store). 

### Key Discoveries & Structural Constraints
Contrary to the official documentation, reverse engineering of the live endpoint reveals the following schema mechanics:

1. **Composition over Inheritance (SOLID Alignment):** Legislators are not represented as distinct object classes like `bcnbio:Senator` or `bcnbio:Deputy`. Instead, all individuals are instances of the generic `http://xmlns.com/foaf/0.1/Person`.
2. **Intermediate Mapping Nodes:** A person is linked to their political roles via an asymmetric relational graph. The property `bcnbio:hasParliamentaryAppointment` points to an intermediate node of type `bcnbio:PositionPeriod`.
3. **Numerical Position IDs:** Roles are encoded numerically within the database:
   * **`1`** = Deputy (*Diputado*)
   * **`2`** = Senator (*Senador*)
4. **Graph-Enclosed Temporal Properties:** Temporal markers (`hasBeginning` and `hasEnd`) do *not* contain raw string or ISO-8601 text literals. They point to separate event nodes (`http://www.w3.org/2006/time#DateTimeDescription`). The actual date string (e.g., `2030-03-11`) must be extracted using the **`bcnbio:originalDate`** property inside those event nodes.
5. **Eager Data Initialization:** The BCN database immediately records the scheduled end date of a parliamentary term when the legislator assumes office. Therefore, looking for "null" or "unbound" end dates will fail. Current validity must be asserted by comparing the current system date against the extracted `bcnbio:originalDate` value of the end node.

---

## 2. API Endpoint & Connection Protocol

* **SPARQL Endpoint URL:** `https://datos.bcn.cl/sparql`
* **HTTP Method:** `GET` (Queries are sent via URL-encoded query parameters)
* **Mandatory HTTP Headers:**
  * `Accept: application/sparql-results+json` (Forces a clean JSON response instead of XML/RDF)
  * `User-Agent: CamaraAbierta-Engine/3.0` (Recommended to avoid automated rate-limiting or blocking)

---

## 3. Comprehensive Property Schema Dictionary

When querying an individual instance of `foaf:Person`, the following properties can be extracted to enrich the profile state in the application frontend:

| RDF Property URI | Data Type / Target | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `http://xmlns.com/foaf/0.1/name` | `xsd:string` | Full official name of the legislator. | `"Álvaro Jorge Carter Fernández"` |
| `http://www.w3.org/2004/02/skos/core#prefLabel` | `xsd:string` | Preferred public or short name. | `"Álvaro Carter Fernández"` |
| `http://datos.bcn.cl/ontologies/bcn-biographies#profession` | `xsd:string` | Registered professional background. | `"Diseñador Industrial"` |
| `http://xmlns.com/foaf/0.1/depiction` | `xsd:anyURI` | URL to the official high-resolution portrait. | `https://www.bcn.cl/laborparlamentaria/imagen/4558.jpg` |
| `http://xmlns.com/foaf/0.1/thumbnail` | `xsd:anyURI` | URL to a cropped 110x110 thumbnail avatar. | `https://www.bcn.cl/laborparlamentaria/imagen/110x110/4558.jpg` |
| `http://datos.bcn.cl/ontologies/bcn-biographies#twitterAccount` | `xsd:string` | Twitter/X handler username. | `"Alvaro_CarterF"` |
| `http://datos.bcn.cl/ontologies/bcn-biographies#bcnPage` | `xsd:anyURI` | External URL to the BCN Parliamentary Wiki page. | `https://www.bcn.cl/historiapolitica/resenas_parlamentarias/wiki/...` |
| `http://datos.bcn.cl/ontologies/bcn-biographies#surnameOfFather` | `xsd:string` | Paternal last name. | `"Carter"` |
| `http://datos.bcn.cl/ontologies/bcn-biographies#surnameOfMother` | `xsd:string` | Maternal last name. | `"Fernández"` |
| `http://xmlns.com/foaf/0.1/gender` | `xsd:string` | Gender identification literal. | `"hombre"` / `"mujer"` |
| `http://datos.bcn.cl/ontologies/bcn-biographies#idCamaraDeDiputados` | `xsd:string` | Internal relational ID for the Chamber website (= OpenData Cámara `Id`). Used as the join key for deputy enrichment against existing `camara:{id}` `bcn_id` records. | `"1017"` |
| `http://datos.bcn.cl/ontologies/bcn-biographies#idSenado` | `xsd:string` | Internal relational ID for the Senate (= wspublico `PARLID` = senado.cl `ID_PARLAMENTARIO`). Used as the join key for senator enrichment against existing `senado:{id}` `bcn_id` records. | `"1234"` |
| `http://datos.bcn.cl/ontologies/bcn-biographies#lastUpdate` | `xsd:dateTime` | Timestamp of the last modifications in BCN. | `2025-07-15T14:53:52-03:00` |

---

## 4. Production-Ready SPARQL Queries

### Query 1: Active Senators and Deputies List (Lazy Loading Pattern)
This optimized query pulls the minimal subset required to populate the list view of the application, resolving the nested date blocks and filtering out historical entries using the system's current execution date.

```sparql
PREFIX bcnbio: <http://datos.bcn.cl/ontologies/bcn-biographies#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>

SELECT DISTINCT ?personUri ?nombre ?cargoId ?fechaInicio ?fechaFin
WHERE {
  # 1. Target generic Person entities and their full names
  ?personUri a foaf:Person .
  ?personUri foaf:name ?nombre .
  
  # 2. Traverse the intermediate Appointment graph
  ?personUri bcnbio:hasParliamentaryAppointment ?nombramiento .
  
  # 3. Extract the Position Type and isolate the numeric trailing ID
  #    ?cargo is a URI like http://datos.bcn.cl/recurso/cl/cargo/2 — strip everything
  #    up to the last slash so the IN filter compares "1"/"2", not the full URI.
  ?nombramiento bcnbio:hasPosition ?cargo .
  BIND(REPLACE(STR(?cargo), ".*/", "") AS ?cargoId)
  
  # Filter constraint: Cargo must be '1' (Deputy) or '2' (Senator)
  FILTER(?cargoId IN ("1", "2"))
  
  # 4. Resolve the nested start date event node
  ?nombramiento bcnbio:hasBeginning ?nodoInicio .
  ?nodoInicio bcnbio:originalDate ?fechaInicio .
  
  # 5. Resolve the nested end date event node
  ?nombramiento bcnbio:hasEnd ?nodoFin .
  ?nodoFin bcnbio:originalDate ?fechaFin .
  
  # 6. Liveness Constraint: Dynamic string comparison against current date
  # REPLACE {CURRENT_DATE} programmatically in runtime (Format: YYYY-MM-DD)
  FILTER(STR(?fechaFin) >= "{CURRENT_DATE}")
}
ORDER BY ?cargoId ?nombre
```

> **Note (2026-05-28):** A previous revision of this query used
> `BIND(STR(?cargo) AS ?cargoId)` with `FILTER(REGEX(?cargoId, "^[12]$"))`. That
> filter never matches, because `?cargoId` is bound to the full URI
> (`http://datos.bcn.cl/recurso/cl/cargo/2`), not the trailing digit. The
> `REPLACE` + `IN` pattern above is what production code must use.

### Query 2: Full Profile Enrichment (Triggered On-Demand)
Executes a single-resource lookup to fetch metadata properties for detailed views inside the frontend application.

```sparql
PREFIX bcnbio: <http://datos.bcn.cl/ontologies/bcn-biographies#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>

SELECT ?nombre ?profesion ?imagen ?thumbnail ?twitter ?paginaWiki ?genero ?apellidoPaterno ?apellidoMaterno
WHERE {
  # Bind the specific target URI dynamically at runtime
  <{TARGET_PERSON_URI}> foaf:name ?nombre .
  
  # Optional clauses ensure structural faults do not crash the pipeline
  OPTIONAL { <{TARGET_PERSON_URI}> bcnbio:profession ?profesion . }
  OPTIONAL { <{TARGET_PERSON_URI}> foaf:depiction ?imagen . }
  OPTIONAL { <{TARGET_PERSON_URI}> foaf:thumbnail ?thumbnail . }
  OPTIONAL { <{TARGET_PERSON_URI}> bcnbio:twitterAccount ?twitter . }
  OPTIONAL { <{TARGET_PERSON_URI}> bcnbio:bcnPage ?paginaWiki . }
  OPTIONAL { <{TARGET_PERSON_URI}> foaf:gender ?genero . }
  OPTIONAL { <{TARGET_PERSON_URI}> bcnbio:surnameOfFather ?apellidoPaterno . }
  OPTIONAL { <{TARGET_PERSON_URI}> bcnbio:surnameOfMother ?apellidoMaterno . }
}
```

---

## 5. Reference Python Implementation

This production-grade script provides a robust reference implementation built with a single-responsibility architecture, comprehensive error handling, and type-safe data parsing.

```python
import requests
import urllib.parse
from datetime import datetime
from typing import List, Dict, Optional, Any

class BCNDataIngestionClient:
    """
    A robust, enterprise-grade client for harvesting active legislative data
    from the BCN Linked Open Data platform.
    """
    
    def __init__(self):
        self.ENDPOINT_URL = "https://datos.bcn.cl/sparql"
        self.HTTP_HEADERS = {
            "Accept": "application/sparql-results+json",
            "User-Agent": "CamaraAbierta-CoreExtractor/3.0"
        }
        self.TIMEOUT_SECONDS = 20

    def fetch_active_legislators(self) -> List[Dict[str, Any]]:
        """
        Harvests all currently serving Senators and Deputies from Chile.
        Uses execution timestamp to ensure dynamic data liveness tracking.
        """
        current_date_str = datetime.now().strftime("%Y-%m-%d")
        
        raw_sparql = f"""
        PREFIX bcnbio: <http://datos.bcn.cl/ontologies/bcn-biographies#>
        PREFIX foaf: <http://xmlns.com/foaf/0.1/>

        SELECT DISTINCT ?personUri ?nombre ?cargoId ?fechaInicio ?fechaFin
        WHERE {{
          ?personUri a foaf:Person .
          ?personUri foaf:name ?nombre .
          
          ?personUri bcnbio:hasParliamentaryAppointment ?nombramiento .
          ?nombramiento bcnbio:hasPosition ?cargo .
          BIND(REPLACE(STR(?cargo), ".*/", "") AS ?cargoId)
          
          FILTER(?cargoId IN ("1", "2"))
          
          ?nombramiento bcnbio:hasBeginning ?nodoInicio .
          ?nodoInicio bcnbio:originalDate ?fechaInicio .
          
          ?nombramiento bcnbio:hasEnd ?nodoFin .
          ?nodoFin bcnbio:originalDate ?fechaFin .
          
          FILTER(STR(?fechaFin) >= "{current_date_str}")
        }}
        ORDER BY ?cargoId ?nombre
        """
        
        encoded_query = urllib.parse.quote(raw_sparql)
        request_url = f"{self.ENDPOINT_URL}?query={encoded_query}&format=application%2Fsparql-results%2Bjson"
        
        try:
            response = requests.get(request_url, headers=self.HTTP_HEADERS, timeout=self.TIMEOUT_SECONDS)
            response.raise_for_status()
            
            payload = response.json()
            bindings = payload.get("results", {}).get("bindings", [])
            return self._transform_bindings(bindings)
            
        except requests.exceptions.RequestException as exc:
            print(f"[FATAL] Network infrastructure failure while communicating with BCN: {exc}")
            return []
        except ValueError:
            print("[FATAL] Payload serialization failure - Response was not standard JSON.")
            return []

    def _transform_bindings(self, bindings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Decouples raw SPARQL graph bindings into sanitized application dictionaries.
        """
        sanitized_records = []
        
        for item in bindings:
            cargo_code = item.get("cargoId", {}).get("value", "")
            role_label = "Deputy" if cargo_code == "1" else "Senator" if cargo_code == "2" else "Unknown"
            
            record = {
                "id": item.get("personUri", {}).get("value", "").split("/")[-1],
                "uri": item.get("personUri", {}).get("value", ""),
                "name": item.get("nombre", {}).get("value", "Unknown"),
                "role": role_label,
                "role_code": cargo_code,
                "term_start": item.get("fechaInicio", {}).get("value", ""),
                "term_end": item.get("fechaFin", {}).get("value", "")
            }
            sanitized_records.append(record)
            
        return sanitized_records

if __name__ == "__main__":
    client = BCNDataIngestionClient()
    print("[INFO] Initializing parliamentary ingestion pipeline...")
    legislators = client.fetch_active_legislators()
    print(f"[SUCCESS] Ingested {len(legislators)} active profiles successfully.")

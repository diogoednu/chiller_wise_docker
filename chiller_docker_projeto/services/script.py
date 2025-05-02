from pymodbus.client import ModbusTcpClient
from pymongo import MongoClient
from CoolProp.CoolProp import PropsSI
import time

# Configurações
fluido = "R410A"
modo_operacao = "frio"  # "frio" ou "quente"

# Mapeamento das variáveis
variables = {
    8960: ("T1_output", "temp"),
    8961: ("T2_output", "temp"),
    8962: ("T3_output", "temp"),
    8963: ("T4_output", "temp"),
    8964: ("T5_output", "temp"),
    8965: ("P1_output", "pressao"),
    8966: ("P2_output", "pressao")
}

# controllers list
controladores = {
    "10.10.0.30": "chiller_1",
}

# Configuração MongoDB
mongo_client = MongoClient("mongodb://mongo:27017")
db = mongo_client["chiller_db"]
collection = db["values"]

# Funções auxiliares
def calc_entalpia(temp_C, pressao_bar):
    try:
        T = temp_C + 273.15
        P = pressao_bar * 1e5
        h = PropsSI("H", "P", P, "T", T, fluido)
        return h / 1000  # kJ/kg
    except Exception as e:
        print(f"Erro ao calcular entalpia: {e}")
        return None

def calc_estado_fluido(temp_C, pressao_bar):
    T = temp_C + 273.15
    P = pressao_bar * 1e5
    try:
        Q = PropsSI("Q", "P", P, "T", T, fluido)
        if Q == 0:
            return "líquido saturado"
        elif Q == 1:
            return "vapor saturado"
        elif 0 < Q < 1:
            return "mistura"
    except:
        pass

    try:
        h = PropsSI("H", "P", P, "T", T, fluido)
        h_liq = PropsSI("H", "P", P, "Q", 0, fluido)
        h_vap = PropsSI("H", "P", P, "Q", 1, fluido)

        if h < h_liq:
            return "líquido sub-resfriado"
        elif h > h_vap:
            return "vapor superaquecido"
        else:
            return "mistura (estimada)"
    except Exception as e:
        print(f"Erro ao determinar estado do fluido: {e}")
        return "erro no cálculo"

# Loop principal
print("A ligar ao controlador Modbus...")

try:
    while True:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        for ip, chiller_name in controladores.items():
            modbus_client = ModbusTcpClient(ip, port=502)
            print(f"[{timestamp}] A conectar ao {chiller_name} ({ip})...")

            if modbus_client.connect():
                doc = {
                    "timestamp": timestamp,
                    "chiller": chiller_name,
                    "ip": ip,
                    "values": {
                        "temps": {},
                        "pressoes": {}
                    }
                }

                for address, (name, tipo) in variables.items():
                    result = modbus_client.read_holding_registers(address=address - 1, count=1)
                    if result.isError():
                        print(f"[{timestamp}] Erro na leitura de {name}")
                        doc["values"]["temps" if tipo == "temp" else "pressoes"][name] = None
                    else:
                        raw = result.registers[0]
                        if raw >= 0x8000:
                            raw -= 0x10000

                        if tipo == "temp":
                            value = raw / 10.0
                            unidade = "ºC"
                            doc["values"]["temps"][name] = value
                        elif tipo == "pressao":
                            value = raw * 0.00429
                            unidade = "bar"
                            doc["values"]["pressoes"][name] = value

                        print(f"[{timestamp}] {name}: {value:.2f} {unidade}")

                # Inserção dos valores brutos
                collection.insert_one(doc)

                temps = doc["values"]["temps"]
                pressoes = doc["values"]["pressoes"]

                if all(k in temps for k in ["T1_output", "T2_output", "T3_output", "T4_output"]) and \
                   all(k in pressoes for k in ["P1_output", "P2_output"]):

                    
                    #modos
                    if modo_operacao == "frio":
                        h1 = round(calc_entalpia(temps["T1_output"], pressoes["P1_output"]), 3)
                        h2 = round(calc_entalpia(temps["T2_output"], pressoes["P2_output"]), 3)
                        h3 = round(calc_entalpia(temps["T3_output"], pressoes["P1_output"]), 3)
                        h4 = round(calc_entalpia(temps["T4_output"], pressoes["P1_output"]), 3)

                        estado_h1 = calc_estado_fluido(temps["T1_output"], pressoes["P1_output"])
                        estado_h2 = calc_estado_fluido(temps["T2_output"], pressoes["P2_output"])
                        estado_h3 = calc_estado_fluido(temps["T3_output"], pressoes["P1_output"])
                        estado_h4 = calc_estado_fluido(temps["T4_output"], pressoes["P1_output"])

                        print(f"[{timestamp}] MODO FRIO: h3 = entrada evaporador, h4 = saída evaporador")

                    elif modo_operacao == "quente":
                        h1 = round(calc_entalpia(temps["T1_output"], pressoes["P1_output"]), 3)
                        h2 = round(calc_entalpia(temps["T2_output"], pressoes["P2_output"]), 3)
                        h3 = round(calc_entalpia(temps["T3_output"], pressoes["P2_output"]), 3)
                        h4 = round(calc_entalpia(temps["T4_output"], pressoes["P2_output"]), 3)

                        estado_h1 = calc_estado_fluido(temps["T1_output"], pressoes["P1_output"])
                        estado_h2 = calc_estado_fluido(temps["T2_output"], pressoes["P2_output"])
                        estado_h3 = calc_estado_fluido(temps["T3_output"], pressoes["P2_output"])
                        estado_h4 = calc_estado_fluido(temps["T4_output"], pressoes["P2_output"])

                        print(f"[{timestamp}] MODO QUENTE: h3 = entrada condensador, h4 = saída condensador")

                    ent_doc = {
                        "timestamp": timestamp,
                        "fluido": fluido,
                        "modo_operacao": modo_operacao,
                        "h1": h1,
                        "estado_h1": estado_h1,
                        "h2": h2,
                        "estado_h2": estado_h2,
                        "h3": h3,
                        "estado_h3": estado_h3,
                        "h4": h4,
                        "estado_h4": estado_h4,
                        "T5_output": temps.get("T5_output")
                    }

                    collection.insert_one(ent_doc)

                    print(f"[{timestamp}] Entalpias gravadas: h1={h1}, h2={h2}, h3={h3}, h4={h4}")
                    print(f"Estados: h1={estado_h1}, h2={estado_h2}, h3={estado_h3}, h4={estado_h4}\n")

                modbus_client.close()
            else:
                print(f"[{timestamp}] Falha na conexão com {ip}")

        time.sleep(10)  
except KeyboardInterrupt:
    print("\nParado pelo utilizador.")
finally:
    mongo_client.close()
    print("Ligações ao MongoDB fechadas.")

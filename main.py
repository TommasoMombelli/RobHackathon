from dotenv import load_dotenv
from cyberwave import Cyberwave


def main() -> None:
    load_dotenv()

    cw = Cyberwave()
    cw.affect("simulation")

    go2 = cw.twin("unitree/go2")
    print(f"Go2 twin: {go2.uuid}")
    print("Simulazione: avanzo di 15 cm...")

    # Con la versione Cyberwave 0.5.0 installata, `distance` e' in metri.
    go2.move_forward(distance=1)


if __name__ == "__main__":
    main()

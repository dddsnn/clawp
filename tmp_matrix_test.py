import asyncio
import os

import nio

client = None


async def message_callback(room: nio.MatrixRoom, event: nio.Event) -> None:
    print(f"received {type(event)}")
    if isinstance(event, nio.RoomMessageText):
        print(
            f"Message received in room {room.display_name}\n"
            f"{room.user_name(event.sender)} | {event.body}"
        )
        return

    elif isinstance(event, nio.KeyVerificationEvent):
        print(f"got key verif {type(event)} with tx id {event.transaction_id}")
        return
    # if isinstance(event,nio.MegolmEvent):
    #     print("trying to decrypt")
    #     try:
    #         print(client.decrypt_event(event))
    #     except Exception as e:
    #         print(e)


async def to_device_callback(event: nio.ToDeviceEvent) -> None:
    print(f"received {type(event)}")
    if isinstance(event, nio.KeyVerificationEvent):
        print(f"got key verif {type(event)} with tx id {event.transaction_id}")
        # await client.accept_key_verification(event.transaction_id)
        return


async def main() -> None:
    global client
    client_config = nio.AsyncClientConfig(
        encryption_enabled=True,
        store_sync_tokens=True,
    )
    client = nio.AsyncClient(
        "https://matrix.org",
        "@dddsnn-test-assistant:matrix.org",
        device_id="test_nio",
        store_path="tmp_nio_store",
        config=client_config,
    )

    client.add_event_callback(message_callback, nio.Event)
    client.add_to_device_callback(to_device_callback, nio.ToDeviceEvent)

    await client.login(os.environ["CLAWP_MATRIX_PASSWORD"])
    client.load_store()
    # devices = await client.devices()
    # print(f"devices: {devices}")
    # for d in devices.devices:
    #     client.verify_device(d)
    if client.should_upload_keys:
        await client.keys_upload()
    try:
        await client.sync_forever(timeout=30000)  # milliseconds
    finally:
        print("closing")
        await client.close()
        print("closed")


asyncio.run(main())

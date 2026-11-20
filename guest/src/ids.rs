//! MAC-48 and EUI-64. The platform names every device by EUI-64 and carries a
//! MAC inside one the way IEEE maps it, `FFFE` between the OUI and the rest:
//! `BC5729049964` is `bc5729fffe049964`.

/// Twelve upper-case hex digits, from any spelling (colons, dashes, case), or None.
pub fn normalize_mac(raw: &str) -> Option<String> {
    let mac: String = raw.chars().filter(char::is_ascii_hexdigit).collect();
    (mac.len() == 12).then(|| mac.to_ascii_uppercase())
}

/// The EUI-64 a MAC is known by, lower case.
pub fn eui_for_mac(mac: &str) -> Option<String> {
    let d = normalize_mac(mac)?.to_ascii_lowercase();
    Some(format!("{}fffe{}", &d[..6], &d[6..]))
}

/// The MAC inside a mapped EUI-64, upper case, or None for any other EUI.
pub fn mac_for_eui(eui: &str) -> Option<String> {
    let e = eui.to_ascii_lowercase();
    let hex = e.len() == 16
        && e.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    (hex && &e[6..10] == "fffe").then(|| format!("{}{}", &e[..6], &e[10..]).to_ascii_uppercase())
}

/// A device key of a scheme and an EUI-64: `ble:bc5729fffe049964`.
pub fn key(scheme: &str, eui: &str) -> String {
    format!("{scheme}:{}", eui.to_ascii_lowercase())
}

/// The adapter name a gateway package speaks as: `<prefix>-<gateway EUI>`.
pub fn adapter(prefix: &str, gateway_eui: &str) -> String {
    format!("{prefix}-{}", gateway_eui.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn macs_and_euis() {
        assert_eq!(
            normalize_mac("bc:57:29:04:99:64").as_deref(),
            Some("BC5729049964")
        );
        assert_eq!(
            eui_for_mac("BC5729049964").as_deref(),
            Some("bc5729fffe049964")
        );
        assert_eq!(
            mac_for_eui("BC5729FFFE049964").as_deref(),
            Some("BC5729049964")
        );
        assert_eq!(mac_for_eui("bc57290000049964"), None);
        assert_eq!(key("ble", "BC5729FFFE049964"), "ble:bc5729fffe049964");
        assert_eq!(adapter("exbg", "AABBCCFFFEDDEEFF"), "exbg-aabbccfffeddeeff");
    }
}

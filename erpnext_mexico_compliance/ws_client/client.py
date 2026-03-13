"""Copyright (c) 2022-2026, TI Sin Problemas and contributors
For license information, please see license.txt"""

import json
import frappe
from frappe import _
from frappe.frappeclient import FrappeClient
from satcfdi.cfdi import CFDI

from . import models

class APIClient(FrappeClient):
    def post_process(self, response):
        try:
            return super().post_process(response)
        except Exception:
            ret = None

        rjson = response.json()
        if rjson and rjson.get("exc_type"):
            msgs = json.loads(rjson.get("_server_messages", "[]"))

            if not msgs:
                msgs = [
                    {
                        "message": response.text,
                        "raise_exception": True,
                        "as_table": False,
                        "indicator": "red",
                    }
                ]

            for m in msgs:
                if isinstance(m, dict):
                    kwargs = m
                else:
                    kwargs = json.loads(m)

                frappe.msgprint(
                    kwargs["message"],
                    _("CFDI Web Service Error"),
                    kwargs["raise_exception"],
                    kwargs["as_table"],
                    indicator=kwargs["indicator"],
                )

        if ret is None:
            frappe.throw(response.text, title=_("CFDI Web Service Error"))
        return ret

    def post_api(self, method, data=None):  # type: ignore
        if data is None:
            data = {}
        res = self.session.post(
            f"{self.url}/api/method/{method}", data=data, verify=self.verify, headers=self.headers
        )
        return self.post_process(res)

    def stamp(self, cfdi: CFDI) -> dict:
        """Stamps the provided CFDI using FINKOK dynamically."""
        import requests
        import re

        # 1. Configuración dinámica desde la interfaz
        settings = frappe.get_single("CFDI Stamping Settings")
        finkok_user = settings.api_key
        finkok_pass = settings.get_password("api_secret")
        
        if not finkok_user or not finkok_pass:
            frappe.throw("Por favor configura tu Usuario y Contraseña de Finkok en 'CFDI Stamping Settings'")

        if settings.test_mode:
            finkok_url = "https://demo-facturacion.finkok.com/servicios/soap/stamp"
        else:
            finkok_url = "https://facturacion.finkok.com/servicios/soap/stamp"

        # 2. Obtener el XML en string puro
        xml_cfdi = cfdi.xml_bytes().decode("utf-8")
        
        # 3. Armar el sobre SOAP
        soap_body = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:stam="http://facturacion.finkok.com/stamp">
           <soapenv:Header/>
           <soapenv:Body>
              <stam:stamp>
                 <stam:xml><![CDATA[{xml_cfdi}]]></stam:xml>
                 <stam:username>{finkok_user}</stam:username>
                 <stam:password>{finkok_pass}</stam:password>
              </stam:stamp>
           </soapenv:Body>
        </soapenv:Envelope>"""

        headers = {'Content-Type': 'text/xml; charset=utf-8'}

        # 4. Enviar a Finkok
        try:
            response = requests.post(finkok_url, data=soap_body.encode('utf-8'), headers=headers)
            texto = response.text
            
            # Buscar si Finkok devolvió un UUID (Timbre Exitoso)
            uuid_match = re.search(r'<UUID>(.*?)</UUID>', texto)
            xml_match = re.search(r'<xml>(.*?)</xml>', texto)
            
            if uuid_match and xml_match:
                from xml.sax.saxutils import unescape
                xml_timbrado = unescape(xml_match.group(1))
                return {"message": {"xml": xml_timbrado}}
            else:
                # Extraer el error de Finkok
                error_match = re.search(r'<Incidencia>.*?<MensajeIncidencia>(.*?)</MensajeIncidencia>', texto, re.DOTALL)
                mensaje_error = error_match.group(1) if error_match else "Error desconocido al timbrar con Finkok"
                frappe.throw(f"Finkok Error: {mensaje_error}", title=_("Error de Timbrado"))
                
        except Exception as e:
            frappe.throw(f"Error de conexión con Finkok: {str(e)}", title=_("Error de Conexión"))


    def cancel_cfdi(self, signing_certificate: str, cfdi: CFDI, reason: str, substitute_uuid: str):
        """Cancels a CFDI using FINKOK dynamically."""
        import requests
        import re

        # 1. Configuración dinámica desde la interfaz
        settings = frappe.get_single("CFDI Stamping Settings")
        finkok_user = settings.api_key
        finkok_pass = settings.get_password("api_secret")

        if settings.test_mode:
            finkok_url = "https://demo-facturacion.finkok.com/servicios/soap/cancel"
        else:
            finkok_url = "https://facturacion.finkok.com/servicios/soap/cancel"

        # 2. Obtener datos del Certificado (CSD) y de la Factura
        csd = frappe.get_doc("Digital Signing Certificate", signing_certificate)
        cer_b64 = csd.get_certificate_b64()
        key_b64 = csd.get_key_b64()
        
        uuid = cfdi["Complemento"]["TimbreFiscalDigital"]["UUID"]
        rfc_emisor = cfdi["Emisor"]["Rfc"]
        
        # Finkok requiere que el campo FolioSustitucion vaya vacío si el motivo no es 01
        sustitucion = substitute_uuid if substitute_uuid else ""

        # 3. Armar el sobre SOAP de Cancelación para Finkok
        soap_body = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:can="http://facturacion.finkok.com/cancel">
           <soapenv:Header/>
           <soapenv:Body>
              <can:cancel>
                 <can:UUIDS>
                    <can:uuids>
                       <can:uuid>{uuid}</can:uuid>
                       <can:Motivo>{reason}</can:Motivo>
                       <can:FolioSustitucion>{sustitucion}</can:FolioSustitucion>
                    </can:uuids>
                 </can:UUIDS>
                 <can:username>{finkok_user}</can:username>
                 <can:password>{finkok_pass}</can:password>
                 <can:taxpayer_id>{rfc_emisor}</can:taxpayer_id>
                 <can:cer>{cer_b64}</can:cer>
                 <can:key>{key_b64}</can:key>
              </can:cancel>
           </soapenv:Body>
        </soapenv:Envelope>"""

        headers = {'Content-Type': 'text/xml; charset=utf-8'}

        # 4. Enviar petición a Finkok
        try:
            response = requests.post(finkok_url, data=soap_body.encode('utf-8'), headers=headers)
            texto = response.text
            
            # Revisar si hay error (Incidencia) en Finkok
            incidencia = re.search(r'<Incidencia>.*?<MensajeIncidencia>(.*?)</MensajeIncidencia>', texto, re.DOTALL)
            if incidencia:
                frappe.throw(f"Error al cancelar en Finkok: {incidencia.group(1)}", title="Error de Cancelación")

            # Extraer el Acuse de recibo del SAT que devuelve Finkok
            acuse_match = re.search(r'<Acuse.*?</Acuse>', texto, re.DOTALL)
            
            if acuse_match:
                from xml.sax.saxutils import unescape
                acuse_xml = unescape(acuse_match.group(0))
                return {"acknowledgement": acuse_xml}
            else:
                frappe.throw("No se recibió el Acuse de cancelación del SAT/Finkok.", title="Error de Cancelación")
                
        except Exception as e:
            frappe.throw(f"Error de conexión con Finkok (Cancelación): {str(e)}", title="Error de Conexión")


    def get_subscription(self):
        """Retrieves the subscription details from the CFDI Web Service."""
        return self.get_api("tisp_apps.api.v1.cfdi.subscription_details")


    def get_status(self, cfdi: CFDI):
        """Retrieves the status of a CFDI from the CFDI Web Service."""
        params = {
            "uuid": cfdi["Complemento"]["TimbreFiscalDigital"]["UUID"],
            "issuer_rfc": cfdi["Emisor"]["Rfc"],
            "receiver_rfc": cfdi["Receptor"]["Rfc"],
            "total": cfdi["Total"],
        }
        response = self.get_api("tisp_apps.api.v1.cfdi.status", params=params)
        return models.CfdiStatus.from_dict(response)
# --- db_supabase.py (VERSÃO APRIMORADA COM NOVAS FUNCIONALIDADES) ---

import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from supabase import create_client, Client
from telegram import User as TelegramUser

logger = logging.getLogger(__name__)

url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
TIMEZONE_BR = timezone(timedelta(hours=-3))

supabase: Client = None
if not url or not key:
    logger.critical("ERRO CRÍTICO: Credenciais do Supabase (URL ou KEY) não encontradas.")
else:
    try:
        supabase: Client = create_client(url, key)
        logger.info("✅ Cliente Supabase criado com sucesso.")
    except Exception as e:
        logger.critical(f"Falha ao criar o cliente Supabase: {e}", exc_info=True)

# --- FUNÇÕES BÁSICAS DE USUÁRIO ---

async def get_or_create_user(tg_user: TelegramUser) -> dict | None:
    """Busca ou cria um usuário no banco de dados."""
    if not supabase:
        return None

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('users')
            .select('id, first_name, username, created_at')
            .eq('telegram_user_id', tg_user.id)
            .execute()
        )

        if response.data:
            user_data = response.data[0]
            # Atualiza informações se mudaram
            if user_data.get('first_name') != tg_user.first_name or user_data.get('username') != tg_user.username:
                await asyncio.to_thread(
                    lambda: supabase.table('users')
                    .update({
                        "first_name": tg_user.first_name,
                        "username": tg_user.username,
                        "updated_at": datetime.now(TIMEZONE_BR).isoformat()
                    })
                    .eq('telegram_user_id', tg_user.id)
                    .execute()
                )
            return user_data
        else:
            # Cria novo usuário
            await asyncio.to_thread(
                lambda: supabase.table('users')
                .insert({
                    "telegram_user_id": tg_user.id,
                    "first_name": tg_user.first_name,
                    "username": tg_user.username,
                    "created_at": datetime.now(TIMEZONE_BR).isoformat()
                })
                .execute()
            )

            new_user_response = await asyncio.to_thread(
                lambda: supabase.table('users')
                .select('id, first_name, username, created_at')
                .eq('telegram_user_id', tg_user.id)
                .execute()
            )

            # Log de novo usuário
            await create_log('user_created', f"Novo usuário cadastrado: {tg_user.id}")

            return new_user_response.data[0] if new_user_response.data else None

    except Exception as e:
        logger.error(f"❌ [DB] Erro em get_or_create_user para {tg_user.id}: {e}", exc_info=True)
        return None

async def find_user_by_id_or_username(identifier: str) -> dict | None:
    """Busca um usuário pelo seu Telegram ID ou username."""
    if not supabase:
        return None

    try:
        query = supabase.table('users').select('*, subscriptions(*, product:products(*))')

        if identifier.isdigit():
            query = query.eq('telegram_user_id', int(identifier))
        else:
            username = identifier[1:] if identifier.startswith('@') else identifier
            query = query.eq('username', username)

        response = await asyncio.to_thread(lambda: query.single().execute())
        return response.data

    except Exception as e:
        if "single result" not in str(e):
            logger.error(f"[DB] Erro ao buscar usuário por '{identifier}': {e}")
        return None

# --- FUNÇÕES DE PRODUTOS ---

async def get_product_by_id(product_id: int) -> dict | None:
    """Busca os detalhes de um produto pelo seu ID."""
    if not supabase:
        return None

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('products')
            .select('*')
            .eq('id', product_id)
            .single()
            .execute()
        )
        return response.data
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar produto {product_id}: {e}", exc_info=True)
        return None

async def get_all_products() -> List[dict]:
    """Retorna todos os produtos cadastrados."""
    if not supabase:
        return []

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('products')
            .select('*')
            .order('price', desc=False)
            .execute()
        )
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar produtos: {e}", exc_info=True)
        return []

# --- FUNÇÕES DE ASSINATURAS ---

async def create_pending_subscription(db_user_id: int, product_id: int, mp_payment_id: str) -> dict | None:
    """Cria um registro de assinatura com status 'pending_payment'."""
    if not supabase:
        return None

    try:
        logger.info(f"💾 [DB] Registrando assinatura pendente para user {db_user_id}, produto {product_id}...")
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .insert({
                "user_id": db_user_id,
                "product_id": product_id,
                "mp_payment_id": mp_payment_id,
                "status": "pending_payment",
                "created_at": datetime.now(TIMEZONE_BR).isoformat()
            })
            .execute()
        )

        await create_log('subscription_pending', f"Assinatura pendente criada: {mp_payment_id}")

        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar assinatura pendente: {e}", exc_info=True)
        return None

async def activate_subscription(mp_payment_id: str) -> dict | None:
    """
    Ativa uma assinatura, definindo as datas de início e fim.
    Se o usuário já tiver uma assinatura ativa, estende a data de término.
    """
    if not supabase:
        return None

    try:
        # 1. Busca a assinatura PENDENTE
        pending_sub_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('*, user:users(*), product:products(*)')
            .eq('mp_payment_id', mp_payment_id)
            .single()
            .execute()
        )

        if not pending_sub_response.data:
            logger.warning(f"⚠️ [DB] Assinatura com mp_payment_id {mp_payment_id} não encontrada.")
            return None

        subscription = pending_sub_response.data
        product = subscription.get('product')
        user = subscription.get('user')

        if not user or not product:
            logger.error(f"❌ [DB] Dados ausentes para assinatura {mp_payment_id}.")
            return None

        if subscription.get('status') == 'active':
            logger.warning(f"⚠️ [DB] Assinatura {subscription['id']} já está ativa.")
            subscription['user'] = {'telegram_user_id': user.get('telegram_user_id')}
            return subscription

        # 2. Verifica se há assinatura ativa para extensão
        active_sub_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id, end_date')
            .eq('user_id', user['id'])
            .eq('status', 'active')
            .neq('id', subscription['id'])
            .order('end_date', desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )

        existing_active_sub = active_sub_response.data
        start_date_base = datetime.now(TIMEZONE_BR)

        # Lógica de extensão
        if existing_active_sub and existing_active_sub.get('end_date'):
            old_end_date = datetime.fromisoformat(existing_active_sub['end_date']).astimezone(TIMEZONE_BR)
            if old_end_date > start_date_base:
                start_date_base = old_end_date
                logger.info(f"✅ [DB] Estendendo assinatura para usuário {user['id']}.")

        # 3. Calcula as novas datas
        new_start_date = datetime.now(TIMEZONE_BR)
        duration_days = product.get('duration_days')
        new_end_date = start_date_base + timedelta(days=duration_days) if duration_days else None

        # 4. Atualiza a assinatura
        update_payload = {
            "status": "active",
            "start_date": new_start_date.isoformat(),
            "end_date": new_end_date.isoformat() if new_end_date else None,
            "updated_at": datetime.now(TIMEZONE_BR).isoformat()
        }

        await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .update(update_payload)
            .eq('id', subscription['id'])
            .execute()
        )

        # Desativa assinatura antiga se houve extensão
        if existing_active_sub:
            await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                .update({'status': 'extended'})
                .eq('id', existing_active_sub['id'])
                .execute()
            )

        # 5. Registra o log
        await create_log(
            'subscription_activated',
            f"Assinatura {subscription['id']} ativada para usuário {user['telegram_user_id']}"
        )

        # 6. Retorna os dados atualizados
        final_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('*, user:users(telegram_user_id)')
            .eq('id', subscription['id'])
            .single()
            .execute()
        )

        logger.info(f"✅ [DB] Assinatura {subscription['id']} ativada com sucesso.")
        return final_response.data if final_response.data else None

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao ativar assinatura {mp_payment_id}: {e}", exc_info=True)
        return None

async def get_user_active_subscription(telegram_user_id: int) -> dict | None:
    """Busca a assinatura ativa de um usuário."""
    if not supabase:
        return None

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('users')
            .select('*, subscriptions(*, product:products(*))')
            .eq('telegram_user_id', telegram_user_id)
            .eq('subscriptions.status', 'active')
            .single()
            .execute()
        )

        if response.data and response.data.get('subscriptions'):
            return response.data['subscriptions'][0]
        return None
    except Exception as e:
        if "single result" not in str(e):
            logger.error(f"❌ [DB] Erro ao buscar assinatura ativa para {telegram_user_id}: {e}")
        return None

async def create_manual_subscription(db_user_id: int, product_id: int, admin_notes: str) -> dict | None:
    """Cria uma assinatura ativa manualmente por um admin."""
    if not supabase:
        return None

    try:
        product = await get_product_by_id(product_id)
        if not product:
            logger.error(f"[DB] Produto {product_id} não encontrado.")
            return None

        start_date = datetime.now(TIMEZONE_BR)
        end_date = None
        if product.get('duration_days'):
            end_date = start_date + timedelta(days=product['duration_days'])

        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .insert({
                "user_id": db_user_id,
                "product_id": product_id,
                "mp_payment_id": admin_notes,
                "status": "active",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat() if end_date else None,
                "created_at": datetime.now(TIMEZONE_BR).isoformat()
            })
            .execute()
        )

        await create_log(
            'manual_subscription',
            f"Assinatura manual criada para usuário {db_user_id} - {admin_notes}"
        )

        logger.info(f"✅ [DB] Assinatura manual criada para usuário {db_user_id}.")
        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar assinatura manual: {e}", exc_info=True)
        return None

async def revoke_subscription(db_user_id: int, admin_notes: str) -> bool:
    """Revoga a assinatura ativa de um usuário."""
    if not supabase:
        return False

    try:
        await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .update({
                "status": "revoked_by_admin",
                "end_date": datetime.now(TIMEZONE_BR).isoformat(),
                "updated_at": datetime.now(TIMEZONE_BR).isoformat()
            })
            .eq('user_id', db_user_id)
            .eq('status', 'active')
            .execute()
        )

        await create_log(
            'subscription_revoked',
            f"Assinatura revogada para usuário {db_user_id} - {admin_notes}"
        )

        logger.info(f"✅ [DB] Assinatura do usuário {db_user_id} revogada.")
        return True

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao revogar assinatura: {e}", exc_info=True)
        return False

async def get_all_active_tg_user_ids() -> list[int]:
    """Retorna uma lista de Telegram User IDs de todos os usuários com assinatura ativa."""
    if not supabase:
        return []

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('user:users(telegram_user_id)')
            .eq('status', 'active')
            .execute()
        )

        if not response.data:
            return []

        user_ids = {item['user']['telegram_user_id'] for item in response.data if item.get('user')}
        return list(user_ids)

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar usuários ativos: {e}", exc_info=True)
        return []

# --- FUNÇÕES DE GRUPOS ---

async def get_all_group_ids() -> list[int]:
    """Busca os IDs de todos os grupos cadastrados."""
    if not supabase:
        return []

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('groups')
            .select('telegram_chat_id')
            .execute()
        )
        return [item['telegram_chat_id'] for item in response.data] if response.data else []

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar IDs dos grupos: {e}", exc_info=True)
        return []

async def get_all_groups_with_names() -> list[dict]:
    """Busca os IDs e nomes de todos os grupos cadastrados."""
    if not supabase:
        return []

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('groups')
            .select('telegram_chat_id, name, created_at')
            .order('name')
            .execute()
        )
        return response.data if response.data else []

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar grupos: {e}", exc_info=True)
        return []

async def add_group(telegram_chat_id: int, name: str) -> bool:
    """Adiciona um novo grupo ao banco de dados."""
    if not supabase:
        return False

    try:
        await asyncio.to_thread(
            lambda: supabase.table('groups')
            .insert({
                "telegram_chat_id": telegram_chat_id,
                "name": name,
                "created_at": datetime.now(TIMEZONE_BR).isoformat()
            })
            .execute()
        )

        await create_log('group_added', f"Grupo adicionado: {name} ({telegram_chat_id})")
        logger.info(f"✅ [DB] Grupo {name} ({telegram_chat_id}) adicionado.")
        return True

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao adicionar grupo: {e}", exc_info=True)
        return False

async def remove_group(telegram_chat_id: int) -> bool:
    """Remove um grupo do banco de dados."""
    if not supabase:
        return False

    try:
        await asyncio.to_thread(
            lambda: supabase.table('groups')
            .delete()
            .eq('telegram_chat_id', telegram_chat_id)
            .execute()
        )

        await create_log('group_removed', f"Grupo removido: {telegram_chat_id}")
        logger.info(f"✅ [DB] Grupo {telegram_chat_id} removido.")
        return True

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao remover grupo: {e}", exc_info=True)
        return False

# --- FUNÇÕES DE CUPONS ---

async def get_coupon_by_code(code: str) -> dict | None:
    """Busca um cupom pelo código."""
    if not supabase:
        return None

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('coupons')
            .select('*')
            .eq('code', code.upper())
            .eq('is_active', True)
            .single()
            .execute()
        )
        return response.data

    except Exception as e:
        if "single result" not in str(e):
            logger.error(f"[DB] Erro ao buscar cupom {code}: {e}")
        return None

async def create_coupon(code: str, discount_type: str, discount_value: float) -> dict | None:
    """Cria um novo cupom de desconto."""
    if not supabase:
        return None

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('coupons')
            .insert({
                "code": code.upper(),
                "discount_type": discount_type,
                "discount_value": discount_value,
                "is_active": True,
                "created_at": datetime.now(TIMEZONE_BR).isoformat()
            })
            .execute()
        )

        await create_log('coupon_created', f"Cupom criado: {code} ({discount_type}: {discount_value})")
        logger.info(f"✅ [DB] Cupom {code} criado.")
        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar cupom: {e}", exc_info=True)
        return None

async def deactivate_coupon(code: str) -> bool:
    """Desativa um cupom."""
    if not supabase:
        return False

    try:
        await asyncio.to_thread(
            lambda: supabase.table('coupons')
            .update({"is_active": False})
            .eq('code', code.upper())
            .execute()
        )

        await create_log('coupon_deactivated', f"Cupom desativado: {code}")
        return True

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao desativar cupom: {e}", exc_info=True)
        return False

# --- FUNÇÕES DE LOGS ---

async def create_log(log_type: str, message: str, user_id: Optional[int] = None) -> bool:
    """Cria um registro de log no banco de dados."""
    if not supabase:
        return False

    try:
        await asyncio.to_thread(
            lambda: supabase.table('logs')
            .insert({
                "type": log_type,
                "message": message,
                "user_id": user_id,
                "created_at": datetime.now(TIMEZONE_BR).isoformat()
            })
            .execute()
        )
        return True

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar log: {e}", exc_info=True)
        return False

async def get_recent_logs(limit: int = 50) -> List[dict]:
    """Busca os logs mais recentes."""
    if not supabase:
        return []

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('logs')
            .select('*')
            .order('created_at', desc=True)
            .limit(limit)
            .execute()
        )
        return response.data if response.data else []

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar logs: {e}", exc_info=True)
        return []

# --- FUNÇÕES DE ESTATÍSTICAS ---

async def get_system_stats() -> Dict[str, Any]:
    """Retorna estatísticas completas do sistema."""
    if not supabase:
        return {}

    try:
        stats = {}

        # Total de usuários
        users_response = await asyncio.to_thread(
            lambda: supabase.table('users').select('id', count='exact').execute()
        )
        stats['total_users'] = users_response.count if hasattr(users_response, 'count') else 0

        # Assinaturas por status
        active_subs = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id', count='exact')
            .eq('status', 'active')
            .execute()
        )
        stats['active_subscriptions'] = active_subs.count if hasattr(active_subs, 'count') else 0

        pending_subs = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id', count='exact')
            .eq('status', 'pending_payment')
            .execute()
        )
        stats['pending_subscriptions'] = pending_subs.count if hasattr(pending_subs, 'count') else 0

        expired_subs = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id', count='exact')
            .eq('status', 'expired')
            .execute()
        )
        stats['expired_subscriptions'] = expired_subs.count if hasattr(expired_subs, 'count') else 0

        # Receita (aproximada - requer campo de preço nas subscriptions)
        # Esta é uma implementação simplificada
        all_active = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('product:products(price)')
            .eq('status', 'active')
            .execute()
        )

        total_revenue = sum(sub['product']['price'] for sub in all_active.data if sub.get('product')) if all_active.data else 0
        stats['total_revenue'] = total_revenue

        # Receita mensal e diária (simplificado)
        today = datetime.now(TIMEZONE_BR).date()
        month_start = today.replace(day=1)

        monthly_subs = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('product:products(price)')
            .eq('status', 'active')
            .gte('created_at', month_start.isoformat())
            .execute()
        )

        monthly_revenue = sum(sub['product']['price'] for sub in monthly_subs.data if sub.get('product')) if monthly_subs.data else 0
        stats['monthly_revenue'] = monthly_revenue

        daily_subs = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('product:products(price)')
            .eq('status', 'active')
            .gte('created_at', today.isoformat())
            .execute()
        )

        daily_revenue = sum(sub['product']['price'] for sub in daily_subs.data if sub.get('product')) if daily_subs.data else 0
        stats['daily_revenue'] = daily_revenue

        # Grupos
        groups_response = await asyncio.to_thread(
            lambda: supabase.table('groups').select('id', count='exact').execute()
        )
        stats['total_groups'] = groups_response.count if hasattr(groups_response, 'count') else 0

        # Cupons ativos
        coupons_response = await asyncio.to_thread(
            lambda: supabase.table('coupons')
            .select('id', count='exact')
            .eq('is_active', True)
            .execute()
        )
        stats['active_coupons'] = coupons_response.count if hasattr(coupons_response, 'count') else 0

        # Taxa de conversão
        total_subs = stats['active_subscriptions'] + stats['pending_subscriptions'] + stats['expired_subscriptions']
        stats['conversion_rate'] = (stats['active_subscriptions'] / total_subs * 100) if total_subs > 0 else 0

        return stats

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar estatísticas: {e}", exc_info=True)
        return {}

async def search_transactions(search_term: str) -> List[dict]:
    """Busca transações por diversos critérios."""
    if not supabase:
        return []

    try:
        # Determina o tipo de busca
        if search_term == 'hoje':
            today = datetime.now(TIMEZONE_BR).date().isoformat()
            response = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                .select('*, user:users(*), product:products(*)')
                .gte('created_at', today)
                .order('created_at', desc=True)
                .execute()
            )
        elif search_term == 'semana':
            week_ago = (datetime.now(TIMEZONE_BR) - timedelta(days=7)).isoformat()
            response = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                .select('*, user:users(*), product:products(*)')
                .gte('created_at', week_ago)
                .order('created_at', desc=True)
                .execute()
            )
        elif search_term.isdigit():
            # Busca por user ID
            response = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                .select('*, user:users(*), product:products(*)')
                .eq('user.telegram_user_id', int(search_term))
                .order('created_at', desc=True)
                .execute()
            )
        elif search_term.startswith('@'):
            # Busca por username
            username = search_term[1:]
            response = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                .select('*, user:users(*), product:products(*)')
                .eq('user.username', username)
                .order('created_at', desc=True)
                .execute()
            )
        else:
            # Busca por payment ID
            response = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                .select('*, user:users(*), product:products(*)')
                .eq('mp_payment_id', search_term)
                .order('created_at', desc=True)
                .execute()
            )

        return response.data if response.data else []

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar transações: {e}", exc_info=True)
        return []

# --- FIM DO ARQUIVO ---
